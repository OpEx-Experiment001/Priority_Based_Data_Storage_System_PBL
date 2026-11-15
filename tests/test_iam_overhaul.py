import unittest
import tempfile
from pathlib import Path

from backend.config import Config
from backend.auth.iam import (
    Role, Permission, create_jwt_token, verify_jwt_token, ROLE_PERMISSIONS
)
from backend.auth.user_manager import UserManager

class TestIAMOverhaul(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.user_db_path = Path(self.temp_dir.name) / "test_users.json"
        self.user_mgr = UserManager(db_path=self.user_db_path)

    def tearDown(self):
        try:
            self.user_mgr.close()
        except Exception:
            pass
        self.temp_dir.cleanup()

    def test_root_admin_seeded_correctly(self):
        # Only admin should be present (no operator or auditor)
        users = self.user_mgr.list_users()
        self.assertEqual(len(users), 1)
        admin = users[0]
        self.assertEqual(admin["username"], "admin")
        self.assertEqual(admin["role"], Role.SUPERADMIN)
        self.assertTrue(admin["is_root"])
        self.assertEqual(admin["extra_permissions"], [])
        self.assertEqual(admin["revoked_permissions"], [])
        self.assertEqual(admin["groups"], [])

        # Cannot delete root admin
        with self.assertRaises(ValueError):
            self.user_mgr.delete_user("admin")

        # Cannot demote root admin
        with self.assertRaises(ValueError):
            self.user_mgr.update_user("admin", role=Role.OPERATOR)

        # Cannot deactivate root admin
        with self.assertRaises(ValueError):
            self.user_mgr.update_user("admin", is_active=False)

    def test_fine_grained_permissions(self):
        # Create an operator user
        self.user_mgr.create_user("alice", "Pass@123", Role.OPERATOR)
        initial_perms = set(self.user_mgr.get_effective_permissions("alice"))
        self.assertIn(Permission.RECORDS_READ, initial_perms)
        self.assertNotIn(Permission.RECORDS_PURGE_ALL, initial_perms)

        # Grant extra permission
        self.user_mgr.grant_permission("alice", Permission.RECORDS_PURGE_ALL)
        after_grant = set(self.user_mgr.get_effective_permissions("alice"))
        self.assertIn(Permission.RECORDS_PURGE_ALL, after_grant)

        # Revoke an existing permission
        self.user_mgr.revoke_permission("alice", Permission.RECORDS_CREATE)
        after_revoke = set(self.user_mgr.get_effective_permissions("alice"))
        self.assertNotIn(Permission.RECORDS_CREATE, after_revoke)

    def test_group_management_and_inheritance(self):
        # Create user
        self.user_mgr.create_user("bob", "Pass@123", Role.AUDITOR)
        bob_perms = set(self.user_mgr.get_effective_permissions("bob"))
        self.assertNotIn(Permission.RECORDS_CREATE, bob_perms)

        # Create group with extra permissions
        self.user_mgr.create_group("data_editors", [Permission.RECORDS_CREATE, Permission.RECORDS_UPDATE], "admin")
        groups = self.user_mgr.list_groups()
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0]["group_name"], "data_editors")

        # Assign user to group
        self.user_mgr.assign_user_to_group("bob", "data_editors")
        bob_effective = set(self.user_mgr.get_effective_permissions("bob"))
        self.assertIn(Permission.RECORDS_CREATE, bob_effective)
        self.assertIn(Permission.RECORDS_UPDATE, bob_effective)

        # Remove user from group
        self.user_mgr.remove_user_from_group("bob", "data_editors")
        bob_after_remove = set(self.user_mgr.get_effective_permissions("bob"))
        self.assertNotIn(Permission.RECORDS_CREATE, bob_after_remove)

    def test_jwt_token_with_effective_permissions(self):
        effective = [Permission.RECORDS_READ, Permission.SYSTEM_EXPORT]
        token = create_jwt_token("carol", Role.OPERATOR, effective_permissions=effective)
        payload = verify_jwt_token(token)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["permissions"], sorted(effective))

if __name__ == "__main__":
    unittest.main()
