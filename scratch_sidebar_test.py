import sys
import os
import time
import django

# Initialize Django and reset admin password within this execution process
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'waba_dashboard.settings')
django.setup()
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.get(username='admin')
u.set_password('admin123')
u.save()
print("Django initialized and admin password set successfully in test process!")

from playwright.sync_api import sync_playwright

def run():
    print("Testing theme button removal...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        
        # Listen for console errors
        page.on("console", lambda msg: print(f"BROWSER CONSOLE [{msg.type}]: {msg.text}"))
        page.on("pageerror", lambda err: print(f"PAGE ERROR: {err}"))

        # 1. Log in as admin
        print("Logging in as admin...")
        page.goto("http://127.0.0.1:8000/login/")
        page.fill("input[name='email']", "digvijaypatil0018@gmail.com")
        page.fill("input[name='password']", "admin123")
        page.click("button[type='submit']")
        page.wait_for_url("http://127.0.0.1:8000/")
        time.sleep(1)

        # 2. Go to Subscribers page
        print("Navigating to Subscribers list...")
        page.goto("http://127.0.0.1:8000/tenants/")
        time.sleep(1.5)

        # Verify Appearance theme toggle button is gone
        theme_toggle = page.locator("text=Dark Mode")
        print(f"Is 'Dark Mode' toggle visible in sidebar? {theme_toggle.is_visible()}")

        # Save screenshot
        screenshot_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\e94f7fc2-b2aa-4e97-b4b8-0b5eca7ff3e8\sidebar_theme_removed.png"
        page.screenshot(path=screenshot_path)
        print(f"Sidebar theme removed screenshot saved to {screenshot_path}")

        browser.close()

if __name__ == '__main__':
    run()
