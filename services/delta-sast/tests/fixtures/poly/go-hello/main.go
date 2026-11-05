package main

import (
	"database/sql"
	"fmt"
	"html"
	"net/http"
)

var db *sql.DB

func hello(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	fmt.Fprintf(w, "<p>Hello %s</p>", name)
}

func safe(w http.ResponseWriter, r *http.Request) {
	name := r.FormValue("name")
	fmt.Fprintf(w, "<p>Hello %s</p>", html.EscapeString(name))
}

func search(w http.ResponseWriter, r *http.Request) {
	q := r.FormValue("q")
	rows, _ := db.Query("SELECT title FROM posts WHERE title LIKE '%" + q + "%'")
	defer rows.Close()
}

func main() {
	http.HandleFunc("/hello", hello)
	http.HandleFunc("/safe", safe)
	http.HandleFunc("/search", search)
	http.ListenAndServe(":8080", nil)
}
