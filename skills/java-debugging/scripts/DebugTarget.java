import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * Test target for the java-debugging skill: a small HTTP server where each endpoint has one known
 * behaviour, so jdb, jcmd, jstat and JFR commands can be checked against a known result before
 * using them on a real application.
 *
 * Endpoints (port given as first argument, default 8081):
 *
 *   /iterate        iterates the shared HashMap, calls visit() once per entry
 *   /remove?key=k3  removes an entry from the shared HashMap (no locking: race with /iterate)
 *   /reset          refills the shared HashMap with k0..k9
 *   /cpu?n=30       CPU work, recursive fib(n)
 *   /alloc?mb=10&retain=true   allocates 1 MB arrays; retain=true keeps them (leak)
 *   /lock?ms=100    holds a shared monitor for ms (lock contention)
 *   /fail           throws IllegalStateException, returns 500
 *   anything else   returns "ok"
 *
 * Every request increments the static field `requests` (watchpoint target: watch DebugTarget.requests).
 * Worker threads are named worker-1..worker-8.
 *
 * Build and run with JDWP on localhost:5006 (-g keeps local variable names for jdb):
 *
 *   mkdir -p /tmp/debug-target
 *   javac -g -d /tmp/debug-target <skill-dir>/scripts/DebugTarget.java
 *   runagent run -n debug-target -- java \
 *       -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=localhost:5006 \
 *       -cp /tmp/debug-target DebugTarget 8081
 *   curl http://127.0.0.1:8081/      # prints "ok"
 *   PID=$(jcmd -l | awk '/DebugTarget/ {print $1}')
 *
 * jdb session (pysess, using scripts/jdb_driver.py; adjust the SKILL.md setup to these ports):
 *
 *   j = Jdb("localhost:5006")     # get() must use http://127.0.0.1:8081
 *
 * Forcing the /iterate vs /remove race (verified on JDK 25, gives HTTP 500 every time):
 *
 *   get("/reset")
 *   j.cmd("stop thread in DebugTarget.visit")       # thread A stops inside the loop
 *   a = pysess.bg(get, "/iterate")
 *   j.collect(["e.getKey()"], count=20, keep=lambda v: v[0] == '"k5"')
 *   j.cmd("clear DebugTarget.visit")
 *   print(get("/remove?key=k8"))                     # thread B changes the map while A waits
 *   j.cmd("catch caught java.util.ConcurrentModificationException")
 *   j.cont()                                         # A continues and its iterator fails
 *   print(j.wait_hit()); print(j.where(5))           # stack at the throw, all threads suspended
 *   j.cmd("ignore caught java.util.ConcurrentModificationException")
 *   j.cmd("resume")
 *   print(a.result(timeout=10))                      # (500, 'java.util.ConcurrentModificationException')
 *
 * Expected results with the metrics tools (verified on JDK 25, recording with settings=profile):
 *
 *   ab -n 300 -c 4 "http://127.0.0.1:8081/cpu?n=24"
 *       jfr view hot-methods: DebugTarget.fib(int) on top
 *   ab -n 40 -c 8 "http://127.0.0.1:8081/lock?ms=200"
 *       jcmd $PID Thread.print (during load): workers BLOCKED, "waiting to lock" in DebugTarget.locked
 *       jfr view contention-by-site: DebugTarget.locked(long)
 *   ab -n 40 -c 2 "http://127.0.0.1:8081/alloc?mb=2&retain=true"
 *       jcmd $PID GC.class_histogram: [B (byte[]) on top, grows per request
 *       jfr view memory-leaks-by-site: DebugTarget.alloc(int, boolean); use the SKILL.md leak recipe
 *       (JFR.start ... memory-leaks=gc-roots, then JFR.dump filename=...)
 *   curl http://127.0.0.1:8081/fail
 *       jfr view exception-by-site: DebugTarget.handle(HttpExchange)
 *
 * The retained arrays stay until the JVM exits. Stop with: runagent delete debug-target
 */
public class DebugTarget {

    static final Map<String, String> shared = new HashMap<>();
    static final Object lock = new Object();
    static final List<byte[]> retained = new ArrayList<>();
    static long requests;

    public static void main(String[] args) throws IOException {
        int port = args.length > 0 ? Integer.parseInt(args[0]) : 8081;
        reset();
        AtomicInteger n = new AtomicInteger();
        HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", port), 0);
        server.createContext("/", DebugTarget::handle);
        server.setExecutor(Executors.newFixedThreadPool(8, r -> new Thread(r, "worker-" + n.incrementAndGet())));
        server.start();
        System.out.println("listening on http://127.0.0.1:" + port);
    }

    static void handle(HttpExchange ex) throws IOException {
        requests++;
        Map<String, String> q = query(ex.getRequestURI().getRawQuery());
        int status = 200;
        String body;
        try {
            body = switch (ex.getRequestURI().getPath()) {
                case "/iterate" -> iterate();
                case "/remove" -> remove(q.getOrDefault("key", "k0"));
                case "/reset" -> reset();
                case "/cpu" -> cpu(Integer.parseInt(q.getOrDefault("n", "30")));
                case "/alloc" -> alloc(Integer.parseInt(q.getOrDefault("mb", "10")),
                        Boolean.parseBoolean(q.getOrDefault("retain", "false")));
                case "/lock" -> locked(Long.parseLong(q.getOrDefault("ms", "100")));
                case "/fail" -> throw new IllegalStateException("requested failure");
                default -> "ok\n";
            };
        } catch (RuntimeException e) {
            status = 500;
            body = e + "\n";
        }
        byte[] b = body.getBytes();
        ex.sendResponseHeaders(status, b.length);
        try (OutputStream out = ex.getResponseBody()) {
            out.write(b);
        }
    }

    static String iterate() {
        int visited = 0;
        for (Map.Entry<String, String> e : shared.entrySet()) {
            visited += visit(e);
        }
        return "visited " + visited + "\n";
    }

    static int visit(Map.Entry<String, String> e) {
        return e.getValue().isEmpty() ? 0 : 1;
    }

    static String remove(String key) {
        shared.remove(key);
        return "removed " + key + ", size " + shared.size() + "\n";
    }

    static String reset() {
        for (int i = 0; i < 10; i++) {
            shared.put("k" + i, "v" + i);
        }
        return "size " + shared.size() + "\n";
    }

    static String cpu(int n) {
        return "fib(" + n + ") = " + fib(n) + "\n";
    }

    static long fib(int n) {
        return n < 2 ? n : fib(n - 1) + fib(n - 2);
    }

    static String alloc(int mb, boolean retain) {
        for (int i = 0; i < mb; i++) {
            byte[] b = new byte[1 << 20];
            b[0] = 1;
            if (retain) {
                retained.add(b);
            }
        }
        return "allocated " + mb + " MB, retained " + retained.size() + " MB\n";
    }

    static String locked(long ms) {
        synchronized (lock) {
            try {
                Thread.sleep(ms);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
            }
        }
        return "held lock " + ms + " ms\n";
    }

    static Map<String, String> query(String raw) {
        Map<String, String> q = new HashMap<>();
        if (raw != null) {
            for (String kv : raw.split("&")) {
                String[] p = kv.split("=", 2);
                q.put(p[0], p.length > 1 ? p[1] : "");
            }
        }
        return q;
    }
}
