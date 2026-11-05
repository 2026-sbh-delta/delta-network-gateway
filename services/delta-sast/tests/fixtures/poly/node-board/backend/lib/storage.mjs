import { readFileSync } from 'node:fs';
import { HttpError } from './http.mjs';

const unavailable = () => new HttpError(503, 'STORAGE_UNAVAILABLE', '저장소에 연결하지 못했어요. 잠시 후 다시 시도해 주세요.');
const limitReached = () => new HttpError(409, 'NOTE_LIMIT_REACHED', '게시글이 1,000개예요. 운영자에게 기존 게시글 정리를 요청해 주세요.');
const validId = (id) => /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id);
const fromRow = (note) => note ? { ...note, createdAt: note.createdAt.toISOString() } : undefined;

export function postgresConfig(env) {
  for (const name of ['PGHOST', 'PGDATABASE', 'PGUSER', 'PGPASSWORD']) {
    if (!env[name]?.trim()) throw new Error(`${name} is required for postgres storage`);
  }
  const port = env.PGPORT ?? '5432';
  if (!/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) throw new Error('PGPORT must be between 1 and 65535');
  const mode = env.PGSSLMODE ?? 'verify-full';
  if (!['disable', 'verify-full'].includes(mode)) throw new Error('PGSSLMODE must be verify-full or local-only disable');
  if (mode === 'disable' && !['127.0.0.1', 'localhost', '::1'].includes(env.PGHOST)) {
    throw new Error('Disabling PostgreSQL TLS is allowed only for a local test database');
  }
  if (mode === 'verify-full' && !env.PGSSLROOTCERT) throw new Error('PGSSLROOTCERT is required for verified PostgreSQL TLS');
  return {
    host: env.PGHOST, port: Number(port), database: env.PGDATABASE,
    user: env.PGUSER, password: env.PGPASSWORD,
    ssl: mode === 'disable' ? false : {
      ca: readFileSync(env.PGSSLROOTCERT, 'utf8'), rejectUnauthorized: true,
      servername: env.PGTLS_SERVERNAME ?? env.PGHOST,
    },
    max: 5, connectionTimeoutMillis: 3000, idleTimeoutMillis: 1000,
    statement_timeout: 5000, query_timeout: 6000, allowExitOnIdle: true,
    application_name: 'delta-sample-backend',
  };
}

export function createStorage(env) {
  const mode = env.STORAGE_BACKEND ?? 'memory';
  if (mode === 'memory') {
    const notes = new Map();
    return {
      mode, health: async () => {}, close: async () => {},
      list: async () => [...notes.values()].reverse(),
      get: async (id) => notes.get(id),
      create: async (note) => {
        if (notes.size >= 1000) throw limitReached();
        notes.set(note.id, note);
      },
      delete: async (id) => notes.delete(id),
    };
  }
  if (mode !== 'postgres') throw new Error('STORAGE_BACKEND must be memory or postgres');
  const config = postgresConfig(env);
  let poolPromise;
  const pool = () => {
    poolPromise ??= import('pg').then(({ Pool }) => {
      const value = new Pool(config);
      // An idle connection failure must not crash the HTTP process.
      value.on('error', () => {});
      return value;
    });
    return poolPromise;
  };
  const database = async (operation) => {
    try { return await operation(await pool()); } catch (error) {
      if (error instanceof HttpError) throw error;
      throw unavailable();
    }
  };
  return {
    mode,
    health: () => database(async (db) => { await db.query('SELECT 1 FROM delta_notes.notes LIMIT 1'); }),
    close: async () => { if (poolPromise) await (await poolPromise).end(); },
    list: () => database(async (db) => (await db.query(
      'SELECT id, title, content, created_at AS "createdAt" FROM delta_notes.notes ORDER BY position DESC',
    )).rows.map(fromRow)),
    get: (id) => {
      if (!validId(id)) return Promise.resolve(undefined);
      return database(async (db) => fromRow((await db.query(
        'SELECT id, title, content, created_at AS "createdAt" FROM delta_notes.notes WHERE id = $1', [id],
      )).rows[0]));
    },
    create: (note) => database(async (db) => {
      const client = await db.connect();
      try {
        await client.query('BEGIN');
        // Serialize the count and insert even when concurrent HTTP requests arrive.
        await client.query('SELECT pg_advisory_xact_lock(17861011)');
        const { rows } = await client.query('SELECT count(*)::integer AS count FROM delta_notes.notes');
        if (rows[0].count >= 1000) throw limitReached();
        await client.query('INSERT INTO delta_notes.notes (id, title, content, created_at) VALUES ($1, $2, $3, $4)',
          [note.id, note.title, note.content, note.createdAt]);
        await client.query('COMMIT');
      } catch (error) {
        try { await client.query('ROLLBACK'); } catch { /* Preserve the original failure. */ }
        throw error;
      } finally { client.release(); }
    }),
    delete: (id) => {
      if (!validId(id)) return false;
      return database(async (db) => (await db.query('DELETE FROM delta_notes.notes WHERE id = $1', [id])).rowCount === 1);
    },
  };
}
