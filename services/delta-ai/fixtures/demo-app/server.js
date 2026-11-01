const express = require("express");
const { Pool } = require("pg");
const { runDiag } = require("./diag");

const app = express();
const pool = new Pool();

app.get("/users", async (req, res) => {
  const result = await pool.query("SELECT id, name FROM users WHERE name = '" + req.query.name + "'");
  res.json(result.rows);
});

app.get("/diag", async (req, res) => {
  res.send(await runDiag(req.query.host));
});

app.listen(8080);
