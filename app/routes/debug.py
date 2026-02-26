"""Debug routes to diagnose Lakebase connection issues"""
import os
from fastapi import APIRouter, Request
from databricks.sdk import WorkspaceClient
import psycopg

router = APIRouter()


@router.get("/lakebase-config")
async def debug_lakebase_config(request: Request):
    """Show current Lakebase configuration (for debugging)"""
    try:
        w = WorkspaceClient()

        # Get environment variables
        env_info = {
            "LAKEBASE_INSTANCE_NAME": os.environ.get("LAKEBASE_INSTANCE_NAME"),
            "PGHOST": os.environ.get("PGHOST"),
            "PGPORT": os.environ.get("PGPORT"),
            "PGDATABASE": os.environ.get("PGDATABASE"),
            "PGUSER": os.environ.get("PGUSER"),
            "DATABRICKS_CLIENT_ID": os.environ.get("DATABRICKS_CLIENT_ID", "not set"),
            "DATABRICKS_CLIENT_SECRET": "set" if os.environ.get("DATABRICKS_CLIENT_SECRET") else "not set",
        }

        # Try to get current user from WorkspaceClient
        try:
            current_user = w.current_user.me()
            identity_info = {
                "user_name": current_user.user_name,
                "display_name": current_user.display_name,
                "active": current_user.active,
            }
        except Exception as e:
            identity_info = {"error": str(e)}

        # Try to get instance info from SDK
        try:
            instance_name = env_info["LAKEBASE_INSTANCE_NAME"]
            inst = w.database.get_database_instance(instance_name)
            sdk_info = {
                "instance_name": inst.name,
                "state": inst.state.value if inst.state else None,
                "read_write_dns": inst.read_write_dns,
            }
        except Exception as e:
            sdk_info = {"error": str(e)}

        # Try to generate token and test connection
        try:
            cred = w.database.generate_database_credential(
                instance_names=[env_info["LAKEBASE_INSTANCE_NAME"]]
            )
            token_info = {
                "token_length": len(cred.token) if cred.token else 0,
                "has_token": bool(cred.token),
            }

            # Try to connect to Lakebase to see the actual error
            try:
                conninfo = request.app.state.lakebase_conninfo
                with psycopg.connect(conninfo, connect_timeout=5) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT current_user")
                        db_user = cur.fetchone()[0]
                        token_info["connection_test"] = "SUCCESS"
                        token_info["connected_as"] = db_user
            except Exception as conn_err:
                token_info["connection_test"] = "FAILED"
                token_info["connection_error"] = str(conn_err)[:500]

        except Exception as e:
            token_info = {"error": str(e)}

        # Get conninfo from app state
        conninfo_status = {
            "has_conninfo": bool(request.app.state.lakebase_conninfo),
            "conninfo_length": len(request.app.state.lakebase_conninfo) if request.app.state.lakebase_conninfo else 0,
        }

        return {
            "env_vars": env_info,
            "workspace_identity": identity_info,
            "sdk_instance_info": sdk_info,
            "token_info": token_info,
            "conninfo_status": conninfo_status,
        }

    except Exception as e:
        return {"error": str(e), "type": type(e).__name__}
