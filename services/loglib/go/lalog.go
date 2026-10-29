// Package lalog is the Go flavour of the shared structured-logging library.
// It writes the common envelope (ts, service, env, level, host, trace_id, msg,
// attrs) as one JSON object per line to stdout for Fluent Bit to ship.
//
//	log := lalog.New("payments")
//	log.Error("Timeout calling bank-gateway after 3000 ms (attempt 3/3)",
//	    lalog.Trace(tid), lalog.Attr("latency_ms", 3012), lalog.Attr("status", 504))
package lalog

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

type Logger struct {
	Service string
	Env     string
	Host    string
	out     io.Writer
	mu      sync.Mutex
}

type entry struct {
	TS      string         `json:"ts"`
	Service string         `json:"service"`
	Env     string         `json:"env"`
	Level   string         `json:"level"`
	Host    string         `json:"host"`
	TraceID string         `json:"trace_id,omitempty"`
	Msg     string         `json:"msg"`
	Attrs   map[string]any `json:"attrs,omitempty"`
}

type Option func(*entry)

func Trace(id string) Option { return func(e *entry) { e.TraceID = id } }

func Attr(k string, v any) Option {
	return func(e *entry) {
		if e.Attrs == nil {
			e.Attrs = map[string]any{}
		}
		e.Attrs[k] = v
	}
}

func New(service string) *Logger {
	env := os.Getenv("LAP_ENV")
	if env == "" {
		env = "dev"
	}
	host, _ := os.Hostname()
	return &Logger{Service: service, Env: env, Host: host, out: os.Stdout}
}

func (l *Logger) log(level, msg string, opts ...Option) {
	e := entry{
		TS:      time.Now().UTC().Format("2006-01-02T15:04:05.000Z"),
		Service: l.Service, Env: l.Env, Level: level, Host: l.Host, Msg: msg,
	}
	for _, o := range opts {
		o(&e)
	}
	b, err := json.Marshal(e)
	if err != nil {
		b = []byte(fmt.Sprintf(`{"ts":%q,"service":%q,"env":%q,"level":"ERROR","msg":"lalog marshal error"}`,
			e.TS, l.Service, l.Env))
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	l.out.Write(append(b, '\n'))
}

func (l *Logger) Debug(msg string, o ...Option) { l.log("DEBUG", msg, o...) }
func (l *Logger) Info(msg string, o ...Option)  { l.log("INFO", msg, o...) }
func (l *Logger) Warn(msg string, o ...Option)  { l.log("WARN", msg, o...) }
func (l *Logger) Error(msg string, o ...Option) { l.log("ERROR", msg, o...) }
func (l *Logger) Fatal(msg string, o ...Option) { l.log("FATAL", msg, o...) }
