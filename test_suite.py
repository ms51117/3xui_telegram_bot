import os
import unittest
import time
import io
import database as db
import cloudflare
import config
from xui_api import XUIClient

class TestBotModules(unittest.TestCase):
    def setUp(self):
        # Use an in-memory or test database
        db.DB_PATH = "test_bot_data.db"
        if os.path.exists(db.DB_PATH):
            os.remove(db.DB_PATH)
        db.init_db()

    def tearDown(self):
        if os.path.exists("test_bot_data.db"):
            try:
                os.remove("test_bot_data.db")
            except Exception:
                pass

    def test_database_crud_and_binding(self):
        # 1. Add user with username
        db.add_or_update_user(
            email="user_ali",
            name="علی رضایی",
            username="@Ali_Rezaei",
            telegram_id=None,
            uuid="1111-2222-3333-4444",
            sub_id="sub12345",
            inbound_id=2
        )
        
        # Verify normalization
        u = db.get_user_by_email("user_ali")
        self.assertIsNotNone(u)
        self.assertEqual(u["username"], "ali_rezaei")
        self.assertEqual(u["name"], "علی رضایی")
        self.assertIsNone(u["telegram_id"])

        # Query by username (with or without @)
        u_by_name = db.get_user_by_username("@ali_rezaei")
        self.assertIsNotNone(u_by_name)
        self.assertEqual(u_by_name["email"], "user_ali")

        # 2. Auto-bind when user sends /start
        bind_res = db.bind_telegram_id_by_username("Ali_Rezaei", 987654321)
        self.assertTrue(bind_res)

        u_bound = db.get_user_by_telegram_id(987654321)
        self.assertIsNotNone(u_bound)
        self.assertEqual(u_bound["email"], "user_ali")
        self.assertEqual(u_bound["telegram_id"], 987654321)

        # 3. Test search_users
        matches = db.search_users("رضایی")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["email"], "user_ali")

        matches_id = db.search_users("987654")
        self.assertEqual(len(matches_id), 1)

        # 4. Delete user
        db.delete_user("user_ali")
        self.assertIsNone(db.get_user_by_email("user_ali"))

    def test_sync_existing_clients(self):
        # Simulate 10 existing clients from 3X-UI where email is numeric Telegram ID
        mock_3xui_clients = [
            {"email": "87384626", "id": "uuid-1", "subId": "sub-1", "enable": True},
            {"email": "12345678", "id": "uuid-2", "subId": "sub-2", "enable": True},
            {"email": "regular_user", "id": "uuid-3", "subId": "sub-3", "enable": False}
        ]
        db.sync_existing_clients(mock_3xui_clients, inbound_id=2)

        # Verify numeric email automatically set as telegram_id
        user1 = db.get_user_by_email("87384626")
        self.assertIsNotNone(user1)
        self.assertEqual(user1["telegram_id"], 87384626)
        self.assertEqual(user1["uuid"], "uuid-1")

        # Verify retrieval by telegram_id
        user2 = db.get_user_by_telegram_id(12345678)
        self.assertIsNotNone(user2)
        self.assertEqual(user2["email"], "12345678")

        all_users = db.list_all_users()
        self.assertEqual(len(all_users), 3)

    def test_vless_url_builder(self):
        uuid_str = "e18c9a96-71bf-48d4-933f-8b9a46d4290c"
        host = "my-tunnel.trycloudflare.com"
        remark = "Ali Rezaei"
        
        config_url = cloudflare.build_vless_config(uuid_str, host, remark)
        expected_start = f"vless://{uuid_str}@{host}:443?path=%2Fvless&security=tls&encryption=none&host={host}&fp=chrome&type=ws&sni={host}#Tunnel-Ali-Rezaei"
        self.assertEqual(config_url, expected_start)

    def test_qr_code_generation(self):
        sample_url = "vless://test@test.com:443"
        bio = cloudflare.generate_qr_code(sample_url)
        self.assertIsInstance(bio, io.BytesIO)
        self.assertGreater(bio.getbuffer().nbytes, 100)

    def test_xui_client_structure(self):
        client = XUIClient()
        self.assertTrue(client.api_prefix.endswith("/"))
        self.assertIn("login", f"{client.api_prefix}login")

if __name__ == '__main__':
    unittest.main()
