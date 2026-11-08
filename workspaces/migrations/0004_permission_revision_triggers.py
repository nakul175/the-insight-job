from typing import ClassVar

from django.db import migrations


def install(apps, editor):
    if editor.connection.vendor == "sqlite":
        editor.execute("""CREATE TRIGGER source_member_revision_update AFTER UPDATE OF active, role ON workspaces_membership
        WHEN NEW.active != OLD.active OR NEW.role != OLD.role BEGIN
          UPDATE workspaces_membership SET generation = OLD.generation + (NEW.active != OLD.active) WHERE id=NEW.id;
          UPDATE workspaces_workspace SET permission_revision=permission_revision + (NEW.active != OLD.active) + (NEW.role != OLD.role) WHERE id=NEW.workspace_id;
        END""")
        for event, ref in [("INSERT", "NEW"), ("DELETE", "OLD")]:
            editor.execute(f"""CREATE TRIGGER source_member_revision_{event.lower()} AFTER {event} ON workspaces_membership BEGIN
              UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id={ref}.workspace_id; END""")
        for table in ["sourcegrant", "systemgrant"]:
            for event, ref in [("INSERT", "NEW"), ("UPDATE", "NEW"), ("DELETE", "OLD")]:
                editor.execute(f"""CREATE TRIGGER source_{table}_{event.lower()} AFTER {event} ON intelligence_{table} BEGIN
                  UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id IN
                  (SELECT workspace_id FROM workspaces_membership WHERE id={ref}.membership_id); END""")
    elif editor.connection.vendor == "postgresql":
        editor.execute("""CREATE FUNCTION source_member_revision_fn() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='UPDATE' THEN
            NEW.generation := OLD.generation + (NEW.active IS DISTINCT FROM OLD.active)::int;
            UPDATE workspaces_workspace SET permission_revision=permission_revision +
              (NEW.active IS DISTINCT FROM OLD.active)::int + (NEW.role IS DISTINCT FROM OLD.role)::int WHERE id=NEW.workspace_id;
            RETURN NEW;
          ELSIF TG_OP='DELETE' THEN
            UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id=OLD.workspace_id; RETURN OLD;
          ELSE
            UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id=NEW.workspace_id; RETURN NEW;
          END IF;
        END $$""")
        editor.execute(
            "CREATE TRIGGER source_member_revision BEFORE INSERT OR UPDATE OR DELETE ON workspaces_membership FOR EACH ROW EXECUTE FUNCTION source_member_revision_fn()"
        )
        editor.execute("""CREATE FUNCTION source_scope_revision_fn() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP='DELETE' THEN
            UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id IN
              (SELECT workspace_id FROM workspaces_membership WHERE id=OLD.membership_id); RETURN OLD;
          ELSE
            UPDATE workspaces_workspace SET permission_revision=permission_revision+1 WHERE id IN
              (SELECT workspace_id FROM workspaces_membership WHERE id=NEW.membership_id); RETURN NEW;
          END IF;
        END $$""")
        for table in ["sourcegrant", "systemgrant"]:
            editor.execute(
                f"CREATE TRIGGER source_scope_revision BEFORE INSERT OR UPDATE OR DELETE ON intelligence_{table} FOR EACH ROW EXECUTE FUNCTION source_scope_revision_fn()"
            )
    else:
        raise RuntimeError("Unsupported permission database.")


def uninstall(apps, editor):
    if editor.connection.vendor == "sqlite":
        for suffix in ["update", "insert", "delete"]:
            editor.execute(f"DROP TRIGGER source_member_revision_{suffix}")
        for table in ["sourcegrant", "systemgrant"]:
            for suffix in ["insert", "update", "delete"]:
                editor.execute(f"DROP TRIGGER source_{table}_{suffix}")
    elif editor.connection.vendor == "postgresql":
        editor.execute("DROP TRIGGER source_member_revision ON workspaces_membership")
        for table in ["sourcegrant", "systemgrant"]:
            editor.execute(
                f"DROP TRIGGER source_scope_revision ON intelligence_{table}"
            )
        editor.execute("DROP FUNCTION source_member_revision_fn()")
        editor.execute("DROP FUNCTION source_scope_revision_fn()")


class Migration(migrations.Migration):
    dependencies: ClassVar[list] = [
        ("workspaces", "0003_portalpermissionreceipt_membership_active_and_more"),
        ("intelligence", "0023_externaldestination_externaloperation_and_more"),
    ]
    operations: ClassVar[list] = [migrations.RunPython(install, uninstall)]
