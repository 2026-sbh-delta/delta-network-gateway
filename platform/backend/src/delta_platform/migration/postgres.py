"""PostgreSQL 17 copy/CDC/promotion for a dedicated service database.

Credentials never enter the approved plan or journal. Network and schema provisioning
are deliberately separate from this driver. Promotion is a one-way safety boundary.
"""

import fcntl
import hashlib
import json
import os
import time
from contextlib import ExitStack
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

from delta_platform.core.store import digest


class MigrationError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise MigrationError(message)


class Journal:
    """Use a persistent, private directory; one process owns a run at a time."""

    def __init__(self, path, plan, *, workflow_id, run_id):
        self.path = Path(path)
        self.plan = plan
        self.workflow_id, self.run_id = str(UUID(str(workflow_id))), str(UUID(str(run_id)))
        self.hash = digest(plan)
        self.state = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(".lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise MigrationError("같은 이전 실행이 이미 진행 중입니다.") from None
        try:
            if self.path.exists():
                self.state = json.loads(self.path.read_text())
                require(self.state.get("plan_hash") == self.hash, "저장한 이전 계획과 다릅니다.")
                require(
                    self.state.get("workflow_id") == self.workflow_id
                    and self.state.get("run_id") == self.run_id,
                    "이전 실행 ID가 다릅니다.",
                )
            else:
                self.state = {
                    "plan_hash": self.hash,
                    "phase": "planned",
                    "history": [],
                    "workflow_id": self.workflow_id,
                    "run_id": self.run_id,
                }
                self.save("planned")
            return self
        except BaseException:
            self.lock.close()
            raise

    def __exit__(self, *args):
        self.lock.close()

    def save(self, phase, **values):
        self.state.update(values, phase=phase, updated_at=time.time())
        if not self.state["history"] or self.state["history"][-1]["phase"] != phase:
            self.state["history"].append({"phase": phase, "at": time.time()})
        temporary = self.path.with_suffix(".tmp")
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(self.state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)
        directory = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def database_identity(connection):
    (system,) = connection.execute(
        "SELECT system_identifier::text FROM pg_control_system()"
    ).fetchone()
    name, version, user = connection.execute(
        "SELECT current_database(), current_setting('server_version_num')::int, current_user"
    ).fetchone()
    require(version // 10000 == 17, "첫 이전 실행기는 PostgreSQL 17만 지원합니다.")
    return {"system_id": system, "database": name, "major_version": 17, "operator": user}


def schema(connection):
    require(
        not connection.execute("SELECT 1 FROM pg_largeobject_metadata LIMIT 1").fetchone(),
        "large object는 별도 이전이 필요합니다.",
    )
    objects = connection.execute("""
        SELECT c.relname,c.relkind,c.relpersistence,c.relrowsecurity
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p','f','m','v') ORDER BY c.relname
    """).fetchall()
    require(0 < len(objects) <= 128, "테이블 수가 지원 범위(1~128)를 벗어났습니다.")
    require(
        all(
            kind == "r" and persistence == "p" and not rls for _, kind, persistence, rls in objects
        ),
        "파티션·뷰·외부/비영속 테이블·RLS는 별도 설계가 필요합니다.",
    )
    require(
        not connection.execute(
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' LIMIT 1"
        ).fetchone(),
        "public 함수는 별도 스키마 검토가 필요합니다.",
    )
    tables = []
    for name, *_ in objects:
        relation = sql.Identifier("public", name).as_string(connection)
        columns = connection.execute(
            """SELECT attname,format_type(atttypid,atttypmod),attnotnull,
            COALESCE(pg_get_expr(d.adbin,d.adrelid),''),attidentity,attgenerated
            FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
            WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""",
            (relation,),
        ).fetchall()
        constraints = connection.execute(
            "SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY conname",
            (relation,),
        ).fetchall()
        indexes = connection.execute(
            "SELECT indexname,indexdef FROM pg_indexes WHERE schemaname='public' AND tablename=%s ORDER BY indexname",
            (name,),
        ).fetchall()
        primary = connection.execute(
            """SELECT a.attname FROM pg_index i
            CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,position)
            JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum
            WHERE i.indrelid=%s::regclass AND i.indisprimary ORDER BY k.position""",
            (relation,),
        ).fetchall()
        require(primary, "모든 이전 테이블에 기본 키가 필요합니다.")
        require(
            not connection.execute(
                "SELECT 1 FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal LIMIT 1",
                (relation,),
            ).fetchone(),
            "사용자 트리거는 별도 검토가 필요합니다.",
        )
        tables.append(
            {
                "name": name,
                "primary": [row[0] for row in primary],
                "columns": columns,
                "constraints": constraints,
                "indexes": indexes,
            }
        )
    sequences = connection.execute("""SELECT sequencename,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size
        FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename""").fetchall()
    # Normalize database tuples before hashing/serializing the plan.
    return json.loads(json.dumps({"tables": tables, "sequences": sequences}))


class PostgresMove:
    def __init__(self, source_dsn, target_dsn, replication_dsn, *, local_testing=False, timeout=90):
        self.source_dsn, self.target_dsn, self.replication_dsn = (
            source_dsn,
            target_dsn,
            replication_dsn,
        )
        self.timeout = timeout
        self.repl = conninfo_to_dict(replication_dsn)
        for dsn in (source_dsn, target_dsn, replication_dsn):
            parsed = conninfo_to_dict(dsn)
            require(
                local_testing or parsed.get("sslmode") == "verify-full",
                "실환경 DB 연결에는 sslmode=verify-full이 필요합니다.",
            )
        source = conninfo_to_dict(source_dsn)
        require(
            all(
                source.get(k, default) == self.repl.get(k, default)
                for k, default in (("host", ""), ("port", "5432"), ("dbname", ""))
            ),
            "복제 연결과 출발 DB 주소가 다릅니다.",
        )

    def __enter__(self):
        self.stack = ExitStack()
        try:
            self.source = self.stack.enter_context(
                psycopg.connect(self.source_dsn, autocommit=True, connect_timeout=5)
            )
            self.target = self.stack.enter_context(
                psycopg.connect(self.target_dsn, autocommit=True, connect_timeout=5)
            )
            self.source_identity = database_identity(self.source)
            self.target_identity = database_identity(self.target)
            require(
                self.source_identity["system_id"] != self.target_identity["system_id"],
                "독립된 출발·대상 PostgreSQL 클러스터가 필요합니다.",
            )
            for connection in (self.source, self.target):
                connection.execute("SET statement_timeout='30s'")
                connection.execute("SET lock_timeout='5s'")
                connection.execute("SET timezone='UTC'")
                connection.execute("SET datestyle='ISO,YMD'")
                (locked,) = connection.execute(
                    "SELECT pg_try_advisory_lock(hashtextextended('delta-transfer:'||current_database(),0))"
                ).fetchone()
                require(locked, "이 DB에서 다른 이전 도구가 실행 중입니다.")
                require(
                    not connection.execute(
                        "SELECT 1 FROM pg_prepared_xacts WHERE database=current_database()"
                    ).fetchone(),
                    "준비된 트랜잭션을 먼저 정리해야 합니다.",
                )
            return self
        except BaseException:
            self.stack.close()
            raise

    def __exit__(self, *args):
        return self.stack.__exit__(*args)

    def _roles(self, connection, names):
        require(
            names and len(names) <= 10 and len(set(names)) == len(names),
            "전용 앱 쓰기 계정을 지정하세요.",
        )
        for name in names:
            role = connection.execute(
                "SELECT rolname,rolsuper,rolreplication,rolcreaterole,rolcreatedb,rolbypassrls FROM pg_roles WHERE rolname=%s",
                (name,),
            ).fetchone()
            require(
                role and not any(role[1:]), "앱 계정에 DB 관리자·복제 권한을 사용할 수 없습니다."
            )
            require(
                not connection.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE usename=%s AND datname<>current_database()",
                    (name,),
                ).fetchone(),
                "다른 DB에서도 쓰는 앱 계정은 자동 전환하지 않습니다.",
            )
        require(
            not connection.execute(
                "SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=ANY(%s) LIMIT 1",
                ([*names, self.repl["user"]],),
            ).fetchone(),
            "앱·복제 계정의 다른 역할 상속은 별도 검토가 필요합니다.",
        )
        require(
            not connection.execute(
                "SELECT 1 FROM pg_tables t, pg_roles r WHERE r.rolname=%s AND t.schemaname='public' AND has_table_privilege(r.oid,quote_ident(t.schemaname)||'.'||quote_ident(t.tablename),'INSERT,UPDATE,DELETE,TRUNCATE') LIMIT 1",
                (self.repl["user"],),
            ).fetchone(),
            "복제 계정에 앱 데이터 쓰기 권한을 주지 않습니다.",
        )
        allowed = [*names, self.repl["user"], connection.info.user]
        unlisted = connection.execute(
            """SELECT DISTINCT r.rolname FROM pg_roles r, pg_tables t
            WHERE r.rolcanlogin AND t.schemaname='public' AND NOT (r.rolname=ANY(%s))
            AND has_table_privilege(r.oid,quote_ident(t.schemaname)||'.'||quote_ident(t.tablename),'INSERT,UPDATE,DELETE,TRUNCATE')""",
            (allowed,),
        ).fetchall()
        require(not unlisted, "등록하지 않은 DB 쓰기 계정이 있습니다. 모든 쓰기 경로를 확인하세요.")

    def plan(self, *, source_environment_id, target_environment_id, writer_roles):
        self._roles(self.source, writer_roles)
        self._roles(self.target, writer_roles)
        source_schema, target_schema = schema(self.source), schema(self.target)
        require(
            source_schema == target_schema,
            "출발·대상 스키마가 다릅니다. 같은 스키마를 먼저 준비하세요.",
        )
        require(
            self.source.execute("SHOW wal_level").fetchone()[0] == "logical",
            "출발 DB의 wal_level=logical 설정이 필요합니다.",
        )
        bound = self.source.execute("SHOW max_slot_wal_keep_size").fetchone()[0]
        require(bound != "-1", "복제 슬롯의 WAL 보관 상한을 서버에 설정하세요.")
        repl = self.source.execute(
            "SELECT rolsuper,rolreplication FROM pg_roles WHERE rolname=%s", (self.repl["user"],)
        ).fetchone()
        require(repl and not repl[0] and repl[1], "전용 비관리자 복제 계정이 필요합니다.")
        for table in source_schema["tables"]:
            relation = sql.Identifier("public", table["name"])
            require(
                self.source.execute(
                    "SELECT has_table_privilege(%s,%s,'SELECT')",
                    (self.repl["user"], relation.as_string(self.source)),
                ).fetchone()[0],
                "복제 계정의 테이블 읽기 권한이 필요합니다.",
            )
            require(
                not self.target.execute(
                    sql.SQL("SELECT 1 FROM {} LIMIT 1").format(relation)
                ).fetchone(),
                "대상 DB에 데이터가 있습니다. 자동으로 지우지 않습니다.",
            )
        return {
            "version": 1,
            "source_environment_id": str(UUID(str(source_environment_id))),
            "target_environment_id": str(UUID(str(target_environment_id))),
            "source": self.source_identity,
            "target": self.target_identity,
            "schema": source_schema,
            "schema_hash": digest(source_schema),
            "writer_roles": sorted(writer_roles),
            "replication_role": self.repl["user"],
        }

    def _bind(self, journal):
        self.journal, self.plan_data = journal, journal.plan
        require(
            journal.plan.get("version") == 1
            and digest(journal.plan["schema"]) == journal.plan["schema_hash"],
            "이전 계획의 형식·스키마 해시가 다릅니다.",
        )
        require(
            journal.plan["source_environment_id"] != journal.plan["target_environment_id"],
            "출발·대상 환경 ID가 같을 수 없습니다.",
        )
        require(journal.hash == digest(journal.plan), "계획 내용이 변경됐습니다.")
        require(
            journal.plan["source"] == self.source_identity
            and journal.plan["target"] == self.target_identity,
            "계획과 DB 신원이 다릅니다.",
        )
        require(journal.plan["replication_role"] == self.repl["user"], "복제 계정이 변경됐습니다.")
        require(
            all(
                digest(schema(c)) == journal.plan["schema_hash"] for c in (self.source, self.target)
            ),
            "이전 중 스키마가 변경됐습니다.",
        )
        self.name = "delta_move_" + UUID(journal.run_id).hex
        self.comment = "delta-transfer:" + journal.hash
        require(
            not self.source.execute(
                "SELECT 1 FROM pg_publication WHERE pubname<>%s LIMIT 1", (self.name,)
            ).fetchone(),
            "다른 복제가 있는 DB는 동시에 이전하지 않습니다.",
        )
        require(
            not self.target.execute(
                "SELECT 1 FROM pg_subscription WHERE subdbid=(SELECT oid FROM pg_database WHERE datname=current_database()) AND subname<>%s LIMIT 1",
                (self.name,),
            ).fetchone(),
            "대상 DB에 다른 복제 구독이 있습니다.",
        )
        for c in (self.source, self.target):
            self._roles(c, journal.plan["writer_roles"])

    def _fence(self, connection):
        for role in self.plan_data["writer_roles"]:
            connection.execute(sql.SQL("ALTER ROLE {} NOLOGIN").format(sql.Identifier(role)))
        deadline = time.monotonic() + self.timeout
        while connection.execute(
            "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND usename=ANY(%s) AND state<>'idle' LIMIT 1",
            (self.plan_data["writer_roles"],),
        ).fetchone():
            require(
                time.monotonic() < deadline,
                "진행 중인 앱 트랜잭션이 끝나지 않았습니다. 쓰기 차단을 유지합니다.",
            )
            time.sleep(0.2)
        connection.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname=current_database() AND usename=ANY(%s)",
            (self.plan_data["writer_roles"],),
        ).fetchall()
        self._wait(
            lambda: (
                not connection.execute(
                    "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND usename=ANY(%s)",
                    (self.plan_data["writer_roles"],),
                ).fetchone()
            ),
            "기존 앱 DB 연결 정리를 기다립니다.",
        )

        require(
            not connection.execute(
                "SELECT 1 FROM pg_prepared_xacts WHERE database=current_database()"
            ).fetchone(),
            "준비된 트랜잭션이 남아 있어 쓰기 차단을 유지합니다.",
        )

    def _wait(self, test, message):
        deadline = time.monotonic() + self.timeout
        while not test():
            require(time.monotonic() < deadline, message)
            time.sleep(0.2)

    def _subscription(self):
        row = self.target.execute(
            "SELECT oid,subenabled,subslotname,subpublications,obj_description(oid,'pg_subscription'),subconninfo FROM pg_subscription WHERE subname=%s AND subdbid=(SELECT oid FROM pg_database WHERE datname=current_database())",
            (self.name,),
        ).fetchone()
        if row:
            require(
                row[2] == self.name
                and row[3] == [self.name]
                and row[4] == self.comment
                and conninfo_to_dict(row[5]) == self.repl,
                "같은 이름의 다른 복제 구독을 변경하지 않습니다.",
            )
        return row

    def _publication(self):
        row = self.source.execute(
            "SELECT oid,obj_description(oid,'pg_publication') FROM pg_publication WHERE pubname=%s",
            (self.name,),
        ).fetchone()
        if row:
            require(row[1] == self.comment, "같은 이름의 다른 publication을 변경하지 않습니다.")
        return row

    def start(self, journal, approved_hash):
        self._bind(journal)
        require(approved_hash == journal.hash, "승인한 이전 계획의 해시가 다릅니다.")
        require(
            journal.state["phase"] in ("planned", "preparing", "copying", "synchronizing"),
            "현재 단계에서는 복제를 새로 시작할 수 없습니다.",
        )
        journal.save("preparing")
        self._fence(self.target)
        for connection in (self.source, self.target):
            connection.execute("CREATE SCHEMA IF NOT EXISTS delta_transfer")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS delta_transfer.barriers (migration_id uuid PRIMARY KEY, marker uuid NOT NULL)"
            )
        self.source.execute(
            sql.SQL("GRANT USAGE ON SCHEMA delta_transfer TO {}").format(
                sql.Identifier(self.repl["user"])
            )
        )
        self.source.execute(
            sql.SQL("GRANT SELECT ON delta_transfer.barriers TO {}").format(
                sql.Identifier(self.repl["user"])
            )
        )
        if not self._publication():
            relations = [
                sql.Identifier("public", table["name"])
                for table in journal.plan["schema"]["tables"]
            ]
            relations.append(sql.Identifier("delta_transfer", "barriers"))
            with self.source.transaction():
                self.source.execute(
                    sql.SQL("CREATE PUBLICATION {} FOR TABLE {}").format(
                        sql.Identifier(self.name), sql.SQL(",").join(relations)
                    )
                )
                self.source.execute(
                    sql.SQL("COMMENT ON PUBLICATION {} IS {}").format(
                        sql.Identifier(self.name), sql.Literal(self.comment)
                    )
                )
        slot = self.source.execute(
            "SELECT plugin,database FROM pg_replication_slots WHERE slot_name=%s", (self.name,)
        ).fetchone()
        if slot:
            require(
                slot == ("pgoutput", self.source_identity["database"]),
                "다른 복제 슬롯을 재사용하지 않습니다.",
            )
        else:
            self.source.execute(
                "SELECT pg_create_logical_replication_slot(%s,'pgoutput')", (self.name,)
            )
        if not self._subscription():
            for table in journal.plan["schema"]["tables"]:
                require(
                    not self.target.execute(
                        sql.SQL("SELECT 1 FROM {} LIMIT 1").format(
                            sql.Identifier("public", table["name"])
                        )
                    ).fetchone(),
                    "구독 생성 전 대상 데이터가 변경됐습니다.",
                )
            with self.target.transaction():
                self.target.execute(
                    sql.SQL(
                        "CREATE SUBSCRIPTION {} CONNECTION {} PUBLICATION {} WITH (create_slot=false, enabled=false, copy_data=true, streaming=off, synchronous_commit=on)"
                    ).format(
                        sql.Identifier(self.name),
                        sql.Literal(self.replication_dsn),
                        sql.Identifier(self.name),
                    )
                )
                self.target.execute(
                    sql.SQL("COMMENT ON SUBSCRIPTION {} IS {}").format(
                        sql.Identifier(self.name), sql.Literal(self.comment)
                    )
                )
        self.target.execute(
            sql.SQL("ALTER SUBSCRIPTION {} ENABLE").format(sql.Identifier(self.name))
        )
        journal.save("copying")
        return self.status(journal)

    def _ready(self):
        row = self._subscription()
        if not row or not row[1]:
            return False
        states = self.target.execute(
            "SELECT srsubstate FROM pg_subscription_rel WHERE srsubid=%s", (row[0],)
        ).fetchall()
        return len(states) == len(self.plan_data["schema"]["tables"]) + 1 and all(
            state == ("r",) for state in states
        )

    def status(self, journal):
        self._bind(journal)
        lag = self.source.execute(
            "SELECT pg_wal_lsn_diff(pg_current_wal_lsn(),confirmed_flush_lsn)::bigint,wal_status FROM pg_replication_slots WHERE slot_name=%s",
            (self.name,),
        ).fetchone()
        ready = self._ready()
        if ready and journal.state["phase"] == "copying":
            journal.save("synchronizing")
        require(
            not lag or lag[1] != "lost",
            "WAL 보관 상한 초과로 복제 슬롯이 유효하지 않습니다. 다시 복사해야 합니다.",
        )
        return {
            "phase": journal.state["phase"],
            "copy_ready": ready,
            "lag_bytes": lag[0] if lag else None,
            "validated": journal.state.get("validated", False),
            "updated_at": journal.state["updated_at"],
        }

    def wait_ready(self, journal):
        self._bind(journal)
        self._wait(
            lambda: self.status(journal)["copy_ready"],
            "초기 복사가 끝나지 않았습니다. 복제 상태를 확인하세요.",
        )
        return self.status(journal)

    def _fingerprint(self, connection, table):
        hashed, count = hashlib.sha256(), 0
        with connection.transaction():
            with connection.cursor(name="delta_verify_" + uuid4().hex) as cursor:
                cursor.execute(
                    sql.SQL("SELECT to_jsonb(t)::text FROM {} t ORDER BY {}").format(
                        sql.Identifier("public", table["name"]),
                        sql.SQL(",").join(sql.Identifier(column) for column in table["primary"]),
                    )
                )
                for (row,) in cursor:
                    hashed.update(row.encode() + b"\n")
                    count += 1
                    require(
                        count <= 1_000_000,
                        "첫 실행기의 전체 대조 한도를 초과했습니다. 대규모 검증 경로가 필요합니다.",
                    )
        return {"rows": count, "sha256": hashed.hexdigest()}

    def promote(self, journal, approved_hash):
        self._bind(journal)
        require(approved_hash == journal.hash, "승인한 전환 계획의 해시가 다릅니다.")
        phase = journal.state["phase"]
        if phase in ("promoted", "cleaned"):
            return self.status(journal)
        require(
            phase
            in (
                "synchronizing",
                "fencing_source",
                "source_fenced",
                "validated",
                "promotion_pending",
            ),
            "복사·동기화가 준비된 뒤 전환할 수 있습니다.",
        )
        if phase != "promotion_pending":
            if phase != "validated":
                require(self._ready(), "모든 테이블의 복제 준비가 필요합니다.")
                journal.save("fencing_source")
                self._fence(self.source)
                self._fence(self.target)
                marker = journal.state.get("final_marker") or str(uuid4())
                journal.save("source_fenced", final_marker=marker)
                self.source.execute(
                    "INSERT INTO delta_transfer.barriers(migration_id,marker) VALUES (%s,%s) ON CONFLICT(migration_id) DO UPDATE SET marker=excluded.marker",
                    (journal.run_id, marker),
                )
                self._wait(
                    lambda: (
                        self.target.execute(
                            "SELECT marker::text FROM delta_transfer.barriers WHERE migration_id=%s",
                            (journal.run_id,),
                        ).fetchone()
                        == (marker,)
                    ),
                    "마지막 변경분이 아직 반영되지 않았습니다. 쓰기 차단을 유지합니다.",
                )
                verified = {}
                for table in journal.plan["schema"]["tables"]:
                    source = self._fingerprint(self.source, table)
                    require(
                        source == self._fingerprint(self.target, table),
                        "최종 데이터 대조에 실패했습니다. 쓰기 차단을 유지합니다.",
                    )
                    verified[table["name"]] = source
                for sequence in journal.plan["schema"]["sequences"]:
                    relation = sql.Identifier("public", sequence[0])
                    value, called = self.source.execute(
                        sql.SQL("SELECT last_value,is_called FROM {}").format(relation)
                    ).fetchone()
                    self.target.execute(
                        "SELECT setval(%s::regclass,%s,%s)",
                        (relation.as_string(self.target), value, called),
                    )
                journal.save(
                    "validated",
                    validated=True,
                    tables=verified,
                    final_source_lsn=self.source.execute(
                        "SELECT pg_current_wal_lsn()::text"
                    ).fetchone()[0],
                )
            self._stop_replication()
            # Never permit automatic rollback after this durable boundary, even if the reply is lost.
            journal.save("promotion_pending")
        else:
            self._stop_replication()
        require(
            not self.source.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s) AND rolcanlogin",
                (journal.plan["writer_roles"],),
            ).fetchone(),
            "출발 DB 쓰기 차단이 해제됐습니다. 새 DB를 열지 않습니다.",
        )
        require(
            not self.source.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname=current_database() AND usename=ANY(%s)",
                (journal.plan["writer_roles"],),
            ).fetchone(),
            "출발 DB의 기존 앱 연결이 남았습니다.",
        )
        for role in journal.plan["writer_roles"]:
            self.target.execute(sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(role)))
        journal.save("promoted")
        return self.status(journal)

    def _stop_replication(self):
        sub = self._subscription()
        if sub:
            self.target.execute(
                sql.SQL("ALTER SUBSCRIPTION {} DISABLE").format(sql.Identifier(self.name))
            )
            self._wait(
                lambda: (
                    not self.target.execute(
                        "SELECT 1 FROM pg_stat_subscription WHERE subid=%s AND pid IS NOT NULL",
                        (sub[0],),
                    ).fetchone()
                ),
                "복제 프로세스가 아직 종료되지 않았습니다.",
            )

    def _remove_replication(self):
        self._stop_replication()
        if self._subscription():
            self.target.execute(sql.SQL("DROP SUBSCRIPTION {}").format(sql.Identifier(self.name)))
        slot = self.source.execute(
            "SELECT active FROM pg_replication_slots WHERE slot_name=%s", (self.name,)
        ).fetchone()
        if slot:
            require(not slot[0], "활성 복제 슬롯을 강제로 지우지 않습니다.")
            self.source.execute("SELECT pg_drop_replication_slot(%s)", (self.name,))
        if self._publication():
            self.source.execute(sql.SQL("DROP PUBLICATION {}").format(sql.Identifier(self.name)))

    def cleanup(self, journal):
        self._bind(journal)
        require(
            journal.state["phase"] in ("promoted", "cleaned"), "DB 승격 뒤 복제 자원을 정리합니다."
        )
        self._remove_replication()
        journal.save("cleaned")
        return self.status(journal)

    def abort_before_promotion(self, journal):
        self._bind(journal)
        require(
            journal.state["phase"] not in ("promotion_pending", "promoted", "cleaned"),
            "새 DB 쓰기가 시작됐을 수 있어 자동 원복하지 않습니다.",
        )
        self._fence(self.target)
        self._remove_replication()
        for role in journal.plan["writer_roles"]:
            self.source.execute(sql.SQL("ALTER ROLE {} LOGIN").format(sql.Identifier(role)))
        journal.save("aborted")
