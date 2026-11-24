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
 * Small HTTP server to practise jdb and JVM diagnostics on. Each endpoint has one known behaviour:
 *
 *   /iterate        iterates the shared HashMap, calls visit() once per entry
 *   /remove?key=k3  removes an entry from the shared HashMap (no locking: race with /iterate)
 *   /reset          refills the shared HashMap
 *   /cpu?n=30       CPU work, recursive fib(n)
 *   /alloc?mb=10&retain=true   allocates 1 MB arrays, optionally keeps them (leak)
 *   /lock?ms=100    holds a shared monitor for ms (lock contention)
 *   /fail           throws IllegalStateException, returns 500
 *
 * Every request increments the static field `requests` (watchpoint target).
 *
 * Build and run (see SKILL.md):
 *   javac -g -d /tmp/debug-target DebugTarget.java
 *   java -agentlib:jdwp=transport=dt_socket,server=y,suspend=n,address=localhost:5006 \
 *        -cp /tmp/debug-target DebugTarget 8081
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
