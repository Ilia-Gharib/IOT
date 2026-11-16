import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv('.env')
from starlette.testclient import TestClient
from app.main import app
import app.auth as auth
from app.ha_client import get_floor_entities_summary

client = TestClient(app)

print('==================== 1. FLOORS & INVITE CODES ====================')
floors = auth.get_all_floors()
for fl in floors:
    print(f"Floor {fl['floor_number']}: {fl['name']} | Invite Code: {fl['invite_code']}")

print('\n==================== 2. REGISTRATION LIVE TEST ====================')
# Attempt wrong invite code
res_bad = client.post('/api/auth/register', json={
    'full_name': 'کاربر تستی',
    'email': 'bad_test@building.local',
    'password': 'password123',
    'floor_code': 'WRONG-123'
})
print('Wrong Invite Code HTTP Status:', res_bad.status_code, '| Error:', res_bad.json().get('detail'))

# Valid registration on Floor 1
res_ok = client.post('/api/auth/register', json={
    'full_name': 'ساکن نمونه طبقه اول',
    'email': 'sample_resident_fl1@building.local',
    'phone': '09121112233',
    'password': 'password123',
    'floor_code': 'FL1-8492'
})
print('Valid Registration HTTP Status:', res_ok.status_code)
user_data = res_ok.json()
print('Registered User:', user_data.get('username'))
print('Locked Floor:', user_data.get('floor'))
print('Allowed Floors:', user_data.get('allowed_floors'))
print('JWT Token:', user_data.get('token')[:45] + '...')

# Clean up sample user from database
conn = auth.get_db_connection()
with conn:
    conn.execute('DELETE FROM users WHERE email = ?', ('sample_resident_fl1@building.local',))
conn.close()
auth.get_user_registry(force_reload=True)
auth.ensure_users_file()
print('Cleaned up sample test user.')

print('\n==================== 3. HA FLOOR 1 ENTITIES (Port 8124) ====================')
f1_entities = get_floor_entities_summary(1)
print(f"Total entities on Floor 1: {len(f1_entities)}")
for e in f1_entities:
    print(f"  - {e['friendly_name']} ({e['entity_id']}) -> State: {e['state_display']}")

print('\n==================== 4. HA FLOOR 2 ENTITIES (Port 8125) ====================')
f2_entities = get_floor_entities_summary(2)
print(f"Total entities on Floor 2: {len(f2_entities)}")
for e in f2_entities:
    print(f"  - {e['friendly_name']} ({e['entity_id']}) -> State: {e['state_display']}")
