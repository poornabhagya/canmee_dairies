import os
import sys
import json
import traceback
import django
from django.urls import get_resolver
from django.test import Client

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'canmee_dairies.settings')
django.setup()

def get_all_urls(urlpatterns, prefix=''):
    routes = []
    for pattern in urlpatterns:
        if hasattr(pattern, 'url_patterns'):
            routes.extend(get_all_urls(pattern.url_patterns, prefix + str(pattern.pattern)))
        else:
            routes.append(prefix + str(pattern.pattern))
    return routes

resolver = get_resolver()
all_urls = get_all_urls(resolver.url_patterns)

# Static routes (parameters නැති endpoints) තෝරා ගැනීම
static_urls = []
for u in all_urls:
    if not any(char in u for char in ['<', '>', '(', ')', '^', '$', '?']):
        clean_url = '/' + u.lstrip('/')
        if clean_url not in static_urls:
            static_urls.append(clean_url)

print(f"\n[+] Total static routes identified: {len(static_urls)}")

client = Client()
errors = []
success_count = 0
protected_count = 0

for url in static_urls:
    try:
        res = client.get(url)
        if res.status_code == 500:
            print(f"[-] CRASH 500: {url}")
            errors.append({
                "url": url,
                "status_code": 500,
                "error": "Internal Server Error (HTTP 500)"
            })
        elif res.status_code in [301, 302, 401, 403]:
            protected_count += 1
        else:
            success_count += 1
    except Exception as e:
        err_msg = str(e)
        stack = traceback.format_exc().splitlines()[-3:] # short traceback
        print(f"[-] ERROR on {url}: {err_msg}")
        errors.append({
            "url": url,
            "status_code": "EXCEPTION",
            "error": err_msg,
            "trace": " | ".join(stack)
        })

total_tested = len(static_urls)
crash_count = len(errors)

print("\n" + "=" * 40)
print(f"Total Routes Tested : {total_tested}")
print(f"Accessible (200/Other): {success_count}")
print(f"RBAC Protected (Redirect/Forbidden): {protected_count}")
print(f"Crashes / 500 Errors : {crash_count}")
print("=" * 40)

# Pipeline Reporting සඳහා සවිස්තරාත්මක JSON ගොනුවක් සෑදීම
summary_data = {
    "total_tested": total_tested,
    "success_count": success_count,
    "protected_count": protected_count,
    "crashes": crash_count,
    "failed_routes": errors,
    "status": "PASSED" if crash_count == 0 else "FAILED"
}

with open("smoke_test_summary.json", "w") as f:
    json.dump(summary_data, f, indent=2)

if crash_count == 0:
    print("[SUCCESS] All endpoints passed with 0 crashes!")
    sys.exit(0)
else:
    print(f"[FAIL] {crash_count} endpoints failed.")
    for err in errors:
        print(f"  ::error title=Smoke Test Failure on {err['url']}::{err.get('error')} ({err.get('status_code')})")
    sys.exit(1)