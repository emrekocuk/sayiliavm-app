"""Her CI çalışmasında kendi geçici 'Apple Distribution' sertifikasını ve App Store
provisioning profilini üretir - hiçbir sertifika/profil GitHub secret olarak saklanmaz,
özel anahtar sadece bu runner'da (RUNNER_TEMP) yaşar ve iş bitince silinir."""
import base64
import json
import os
import time
import urllib.error
import urllib.request

import jwt

BUNDLE_ID = "com.sayiliavm.app"
CERT_NAME_PREFIX = "Sayili AVM CI"
PROFILE_NAME = "Sayili AVM App Store CI"

runner_temp = os.environ["RUNNER_TEMP"]
key_id = os.environ["APPLE_API_KEY_ID"]
issuer_id = os.environ["APPLE_API_ISSUER_ID"]

with open(os.path.expanduser(f"~/private_keys/AuthKey_{key_id}.p8")) as f:
    private_key = f.read()

token = jwt.encode(
    {"iss": issuer_id, "iat": int(time.time()), "exp": int(time.time()) + 1200, "aud": "appstoreconnect-v1"},
    private_key, algorithm="ES256", headers={"kid": key_id},
)


def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"https://api.appstoreconnect.apple.com/v1{path}",
        data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req) as r:
            text = r.read().decode()
            return r.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def list_all(path):
    items = []
    next_path = path
    while next_path:
        status, resp = call("GET", next_path)
        if status != 200:
            raise RuntimeError(f"GET {next_path} failed: {status} {resp}")
        items.extend(resp["data"])
        next_link = resp.get("links", {}).get("next")
        next_path = next_link.replace("https://api.appstoreconnect.apple.com/v1", "") if next_link else None
    return items


# 1) Onceki CI calismalarindan kalan sertifika/profilleri temizle (Apple'da max 3 Apple
#    Distribution sertifikasina izin var, birikmesin).
for cert in list_all("/certificates?filter[certificateType]=IOS_DISTRIBUTION"):
    name = cert["attributes"].get("displayName") or cert["attributes"].get("name") or ""
    if name.startswith(CERT_NAME_PREFIX):
        call("DELETE", f"/certificates/{cert['id']}")
        print("eski sertifika silindi:", cert["id"])

for profile in list_all("/profiles?filter[profileType]=IOS_APP_STORE"):
    if profile["attributes"].get("name") == PROFILE_NAME:
        call("DELETE", f"/profiles/{profile['id']}")
        print("eski profil silindi:", profile["id"])

# 2) Yeni sertifika olustur (CSR bu calismada runner'da uretildi, ozel anahtari asla Apple'a gitmez)
with open(f"{runner_temp}/dist.csr") as f:
    csr_content = f.read()

status, resp = call("POST", "/certificates", {
    "data": {
        "type": "certificates",
        "attributes": {"csrContent": csr_content, "certificateType": "IOS_DISTRIBUTION"},
    }
})
if status not in (200, 201):
    raise RuntimeError(f"sertifika olusturma basarisiz: {status} {resp}")
cert_id = resp["data"]["id"]
cert_b64 = resp["data"]["attributes"]["certificateContent"]
with open(f"{runner_temp}/dist.cer", "wb") as f:
    f.write(base64.b64decode(cert_b64))
print("yeni sertifika olusturuldu:", cert_id)

# 3) Bundle ID'yi bul
status, resp = call("GET", f"/bundleIds?filter[identifier]={BUNDLE_ID}")
if status != 200 or not resp["data"]:
    raise RuntimeError(f"bundle id bulunamadi: {status} {resp}")
bundle_id_resource = resp["data"][0]["id"]

# 4) Yeni App Store profili olustur (yeni sertifikaya bagli)
status, resp = call("POST", "/profiles", {
    "data": {
        "type": "profiles",
        "attributes": {"name": PROFILE_NAME, "profileType": "IOS_APP_STORE"},
        "relationships": {
            "bundleId": {"data": {"type": "bundleIds", "id": bundle_id_resource}},
            "certificates": {"data": [{"type": "certificates", "id": cert_id}]},
        },
    }
})
if status not in (200, 201):
    raise RuntimeError(f"profil olusturma basarisiz: {status} {resp}")
profile = resp["data"]["attributes"]
out_path = os.path.expanduser(f"~/Library/MobileDevice/Provisioning Profiles/{profile['uuid']}.mobileprovision")
with open(out_path, "wb") as f:
    f.write(base64.b64decode(profile["profileContent"]))
print("yeni profil kuruldu:", profile["name"], profile["uuid"])
