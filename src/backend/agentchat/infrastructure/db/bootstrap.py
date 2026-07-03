from loguru import logger
from sqlmodel import SQLModel, select, text

from agentchat.api.services.user import UserService
from agentchat.database import engine, ensure_mysql_database, import_enterprise_rag_models
from agentchat.database.models.role import Role
from agentchat.database.models.user import UserTable
from agentchat.database.models.user_role import UserRole
from agentchat.database.session import session_getter


_PARSE_JOB_COLUMNS = {
    "total_pages": "INT NULL",
    "parsed_pages": "INT NOT NULL DEFAULT 0",
    "indexed_chunks": "INT NOT NULL DEFAULT 0",
    "current_stage": "VARCHAR(32) NULL",
    "checkpoint_json": "LONGTEXT NULL",
    "trace_json": "LONGTEXT NULL",
}

_CHUNK_MANIFEST_COLUMNS = {
    "content_hash": "VARCHAR(64) NOT NULL DEFAULT ''",
    "index_version": "INT NOT NULL DEFAULT 1",
    "status": "VARCHAR(32) NOT NULL DEFAULT 'active'",
}


async def init_enterprise_rag_database() -> None:
    ensure_mysql_database()
    import_enterprise_rag_models()
    SQLModel.metadata.create_all(engine)
    _ensure_parse_job_columns()
    _ensure_chunk_manifest_columns()
    _ensure_demo_identity()


def _ensure_chunk_manifest_columns() -> None:
    with engine.connect() as conn:
        existing = {
            row[0]
            for row in conn.execute(text("SHOW COLUMNS FROM document_chunk")).fetchall()
        }
        for column, ddl in _CHUNK_MANIFEST_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE document_chunk ADD COLUMN {column} {ddl}"))
            logger.info("已为 document_chunk 添加列 {}", column)
        conn.commit()


def _ensure_parse_job_columns() -> None:
    with engine.connect() as conn:
        existing = {
            row[0]
            for row in conn.execute(text("SHOW COLUMNS FROM document_parse_job")).fetchall()
        }
        for column, ddl in _PARSE_JOB_COLUMNS.items():
            if column in existing:
                continue
            conn.execute(text(f"ALTER TABLE document_parse_job ADD COLUMN {column} {ddl}"))
            logger.info("已为 document_parse_job 添加列 {}", column)
        conn.commit()


def _ensure_demo_identity() -> None:
    with session_getter() as session:
        admin_role = session.get(Role, 1)
        if not admin_role:
            session.add(Role(id=1, role_name="管理员", remark="access_level=100"))
        employee_role = session.get(Role, 2)
        if not employee_role:
            session.add(Role(id=2, role_name="普通员工", remark="access_level=10"))

        _ensure_demo_user(session, "1", "Admin", "admin@example.com", "admin123")
        _ensure_demo_user(session, "2", "employee", "employee@example.com", "employee123")

        _ensure_user_role(session, "1", "1")
        _ensure_user_role(session, "2", "2")
        session.commit()


def _ensure_user_role(session, user_id: str, role_id: str) -> None:
    exists = session.exec(
        select(UserRole).where(UserRole.user_id == user_id).where(UserRole.role_id == role_id)
    ).first()
    if not exists:
        session.add(UserRole(id=f"{user_id}_{role_id}", user_id=user_id, role_id=role_id))


def _ensure_demo_user(
    session,
    user_id: str,
    user_name: str,
    user_email: str,
    user_password: str,
) -> None:
    encrypted_password = UserService.encrypt_sha256_password(user_password)
    user = session.get(UserTable, user_id)
    if not user:
        session.add(
            UserTable(
                user_id=user_id,
                user_name=user_name,
                user_email=user_email,
                user_password=encrypted_password,
                user_avatar="",
            )
        )
        return

    user.user_name = user_name
    user.user_email = user_email
    user.user_password = encrypted_password
    user.delete = False
    session.add(user)
