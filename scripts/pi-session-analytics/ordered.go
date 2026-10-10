package main

import (
	"bytes"
	"encoding/json"
	"sort"
)

// OrderedFloats is a float map that preserves insertion order when marshaled.
type OrderedFloats struct {
	keys []string
	vals map[string]float64
}

func (o *OrderedFloats) Add(key string, v float64) {
	if o.vals == nil {
		o.vals = map[string]float64{}
	}
	if _, ok := o.vals[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.vals[key] += v
}

func (o *OrderedFloats) Get(key string) float64 { return o.vals[key] }

func (o *OrderedFloats) Len() int { return len(o.keys) }

func (o *OrderedFloats) Keys() []string { return o.keys }

// SortedByValueDesc returns keys ordered by descending value (ties keep
// insertion order).
func (o *OrderedFloats) SortedByValueDesc() []string {
	keys := append([]string(nil), o.keys...)
	sort.SliceStable(keys, func(i, j int) bool { return o.vals[keys[i]] > o.vals[keys[j]] })
	return keys
}

func (o OrderedFloats) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, _ := json.Marshal(k)
		vb, _ := json.Marshal(o.vals[k])
		b.Write(kb)
		b.WriteByte(':')
		b.Write(vb)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}

// OrderedInts is the integer counterpart of OrderedFloats.
type OrderedInts struct {
	keys []string
	vals map[string]int64
}

func (o *OrderedInts) Add(key string, v int64) {
	if o.vals == nil {
		o.vals = map[string]int64{}
	}
	if _, ok := o.vals[key]; !ok {
		o.keys = append(o.keys, key)
	}
	o.vals[key] += v
}

func (o *OrderedInts) Inc(key string) { o.Add(key, 1) }

func (o *OrderedInts) Get(key string) int64 { return o.vals[key] }

func (o *OrderedInts) Len() int { return len(o.keys) }

func (o *OrderedInts) Keys() []string { return o.keys }

// SortedByValueDesc returns keys ordered by descending value (ties keep
// insertion order).
func (o *OrderedInts) SortedByValueDesc() []string {
	keys := append([]string(nil), o.keys...)
	sort.SliceStable(keys, func(i, j int) bool { return o.vals[keys[i]] > o.vals[keys[j]] })
	return keys
}

func (o OrderedInts) MarshalJSON() ([]byte, error) {
	var b bytes.Buffer
	b.WriteByte('{')
	for i, k := range o.keys {
		if i > 0 {
			b.WriteByte(',')
		}
		kb, _ := json.Marshal(k)
		b.Write(kb)
		b.WriteByte(':')
		vb, _ := json.Marshal(o.vals[k])
		b.Write(vb)
	}
	b.WriteByte('}')
	return b.Bytes(), nil
}
