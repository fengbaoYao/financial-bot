"""Real PostgreSQL RLS integration test.

Skipped when a local PostgreSQL server/psql is unavailable. The test creates an
isolated database, installs the minimal Supabase auth objects, applies the real
migration, and then verifies both application-level owner filters and database
RLS. It never touches an existing application database.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

from api.financial_bot.db import Database
from api.financial_bot.store import FinancialBotStore, NotFoundError

MIGRATIONS = [
    Path(__file__).resolve().parents[1] / "supabase" / "migrations" / name
    for name in (
        "0006_financial_bot.sql",
        "0007_financial_bots.sql",
        "0008_bot_memory_scope.sql",
        "0009_financial_monitors.sql",
        "0010_monitor_events.sql",
        "0011_bot_channel_settings.sql",
        "0012_notification_digest.sql",
        "0013_delivery_payload.sql",
        "0014_delivery_retry_queue.sql",
        "0015_silence_and_closures.sql",
        "0016_bot_prompt_studio.sql",
        "0017_task_ui_context.sql",
        "0018_task_plans.sql",
        "0019_task_contract.sql",
    )
]
OWNER_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OWNER_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def _psql(*args: str, database: str = "postgres") -> str:
    result = subprocess.run(
        ["psql", "-d", database, "-v", "ON_ERROR_STOP=1", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def _postgres_available() -> bool:
    if not shutil.which("psql"):
        return False
    try:
        _psql("-Atc", "select 1")
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _postgres_available(), reason="local PostgreSQL/psql is not available"
)


@pytest.fixture()
def postgres_db():
    name = f"financial_bot_rls_{uuid.uuid4().hex[:10]}"
    _psql("-c", f'create database "{name}"')
    try:
        _psql(
            "-c",
            """
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
            end $$;
            """,
            database=name,
        )
        _psql(
            "-c",
            """
            create schema if not exists auth;
            create or replace function auth.uid() returns uuid
            language sql stable
            as $$
              select nullif(current_setting('request.jwt.claims', true)::json->>'sub', '')::uuid
            $$;
            """,
            database=name,
        )
        for migration in MIGRATIONS:
            _psql("-f", str(migration), database=name)
        yield name
    finally:
        subprocess.run(
            [
                "psql",
                "-d",
                "postgres",
                "-Atc",
                f"""select pg_terminate_backend(pid) from pg_stat_activity where datname = '{name}'""",
            ],
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["psql", "-d", "postgres", "-c", f'drop database if exists "{name}"'],
            capture_output=True,
            text=True,
        )


def test_application_store_isolates_owners_on_postgres(postgres_db):
    database = Database(database_url=f"postgresql:///{postgres_db}")
    database.initialize()
    store = FinancialBotStore(database)

    memory = store.add_direct_memory(
        OWNER_A,
        kind="holding",
        text="持有腾讯 100 股",
        source_text="我持有腾讯 100 股",
        source_ref="task:t1",
    )
    task, _ = store.create_task(
        owner_id=OWNER_A,
        query="研究腾讯",
        session_id=None,
        language="zh-CN",
        research_mode="quick",
        idempotency_key="pg-1",
    )
    candidate = store.add_candidate(
        OWNER_A,
        task_id=task["id"],
        proposed_text="腾讯的广告业务可能受益于视频号商业化。",
        rationale="示例",
        source_text="回答节选",
        source_ref="task:t1",
    )

    assert store.get_memory(OWNER_A, memory["id"])["text"] == "持有腾讯 100 股"
    with pytest.raises(NotFoundError):
        store.get_memory(OWNER_B, memory["id"])
    with pytest.raises(NotFoundError):
        store.get_task(OWNER_B, task["id"])
    assert store.list_memories(OWNER_B) == []
    assert store.list_candidates(OWNER_B) == []
    with pytest.raises(NotFoundError):
        store.list_events(OWNER_B, task["id"])
    with pytest.raises(NotFoundError):
        store.confirm_candidate(
            OWNER_B,
            candidate["id"],
            expected_version=1,
            final_text="篡改",
            idempotency_key="pg-tamper",
        )

    # Deletion by B must not remove A's memory.
    store.request_deletion(OWNER_B, scope="all", request_id="pg-del-b")
    assert store.get_memory(OWNER_A, memory["id"])["text"] == "持有腾讯 100 股"


def test_rls_blocks_cross_owner_read_write_even_without_app_filters(postgres_db):
    import psycopg

    dsn = f"postgresql:///{postgres_db}"
    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": OWNER_A, "role": "authenticated"}),),
        )
        conn.execute(
            """
            INSERT INTO public.fb_memories
                (id, owner_id, kind, text, source_text, source_ref, status,
                 version, text_hash, created_at, updated_at, confirmed_at)
            VALUES ('mem_rls_a', %s, 'watch_object', '腾讯', '我关注腾讯', 'task:t1',
                    'confirmed', 1, 'hash-a', '2026-10-28T00:00:00+00:00',
                    '2026-10-28T00:00:00+00:00', '2026-10-28T00:00:00+00:00')
            """,
            (OWNER_A,),
        )
        conn.commit()

    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": OWNER_B, "role": "authenticated"}),),
        )
        count = conn.execute("SELECT count(*) FROM public.fb_memories").fetchone()[0]
        assert count == 0
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                """
                INSERT INTO public.fb_memories
                    (id, owner_id, kind, text, source_text, source_ref, status,
                     version, text_hash, created_at, updated_at, confirmed_at)
                VALUES ('mem_rls_bad', %s, 'watch_object', '篡改', '篡改', 'x',
                        'confirmed', 1, 'hash-b', '2026-10-28T00:00:00+00:00',
                        '2026-10-28T00:00:00+00:00', '2026-10-28T00:00:00+00:00')
                """,
                (OWNER_A,),
            )
            conn.commit()
        assert "row-level security" in str(excinfo.value).lower()
        conn.rollback()

    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": OWNER_A, "role": "authenticated"}),),
        )
        count = conn.execute("SELECT count(*) FROM public.fb_memories").fetchone()[0]
        assert count == 1


def test_concurrent_candidate_confirmation_yields_one_memory(postgres_db):
    from concurrent.futures import ThreadPoolExecutor

    from api.financial_bot.store import ConflictError

    database = Database(database_url=f"postgresql:///{postgres_db}")
    database.initialize()
    store = FinancialBotStore(database)
    candidate = store.add_candidate(
        OWNER_A,
        task_id=None,
        proposed_text="并发确认测试：腾讯广告收入可能继续增长。",
        rationale="fixture",
        source_text="fixture",
        source_ref="task:t1",
    )

    def confirm(index: int):
        try:
            candidate_after, memory = store.confirm_candidate(
                OWNER_A,
                candidate["id"],
                expected_version=1,
                final_text="腾讯广告收入可能继续增长。",
                idempotency_key=f"concurrent-{index}",
            )
            return ("ok", memory["id"])
        except ConflictError:
            return ("conflict", None)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(confirm, (1, 2)))
    statuses = sorted(result[0] for result in results)
    assert statuses == ["conflict", "ok"]
    memories = [row for row in store.list_memories(OWNER_A) if row["kind"] == "confirmed_viewpoint"]
    assert len(memories) == 1


def test_dispositions_and_platform_state_respect_rls(postgres_db):
    import psycopg

    dsn = f"postgresql:///{postgres_db}"

    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": OWNER_A, "role": "authenticated"}),),
        )
        conn.execute(
            """
            INSERT INTO public.fb_dispositions
                (id, owner_id, task_id, session_id, input_ref, disposition,
                 reason_code, reason_display, created_at)
            VALUES ('disp_rls_a', %s, 'task_1', 's1', 'msg-1', 'accepted',
                    'research_started', '已开始研究', '2026-11-02T00:00:00+00:00')
            """,
            (OWNER_A,),
        )
        conn.commit()

    with psycopg.connect(dsn) as conn:
        conn.execute("SET ROLE authenticated")
        conn.execute(
            "SELECT set_config('request.jwt.claims', %s, true)",
            (json.dumps({"sub": OWNER_B, "role": "authenticated"}),),
        )
        count = conn.execute("SELECT count(*) FROM public.fb_dispositions").fetchone()[0]
        assert count == 0
        with pytest.raises(Exception) as excinfo:
            conn.execute(
                """
                INSERT INTO public.fb_dispositions
                    (id, owner_id, input_ref, disposition, created_at)
                VALUES ('disp_rls_bad', %s, 'msg-2', 'accepted', '2026-11-02T00:00:00+00:00')
                """,
                (OWNER_A,),
            )
            conn.commit()
        assert "row-level security" in str(excinfo.value).lower()
        conn.rollback()

        # SET ROLE is transactional, so re-enter the authenticated role.
        conn.execute("SET ROLE authenticated")
        # Platform pause is operations-only: the authenticated role has no grant.
        with pytest.raises(Exception) as pause_error:
            conn.execute("SELECT * FROM public.fb_platform_state")
        assert "permission denied" in str(pause_error.value).lower()
        conn.rollback()
