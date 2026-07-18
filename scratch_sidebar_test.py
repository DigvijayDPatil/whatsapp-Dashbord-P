import sys
import os
import time
from playwright.sync_api import sync_playwright

def run():
    print("Starting sidebar test and screenshots capture...")
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=True)
        except Exception as e:
            print("Failed to launch chromium: " + str(e))
            return

        page = browser.new_page()
        page.set_viewport_size({"width": 1280, "height": 800})
        
        # Log in
        page.goto("http://127.0.0.1:8000/login/")
        page.fill("input[name='email']", "admin@example.com")
        page.fill("input[name='password']", "admin123")
        page.click("button[type='submit']")
        page.wait_for_url("http://127.0.0.1:8000/")
        time.sleep(2)
        
        # Save Expanded Sidebar screenshot
        expanded_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\636ab0d7-eed3-45b0-8a2d-3d32e77c2a5c\sidebar_expanded.png"
        page.screenshot(path=expanded_path)
        print("Expanded screenshot saved to " + expanded_path)
        
        # Click the Toggle Sidebar button
        # Selector: button[title="Toggle Sidebar"]
        toggle_button = page.locator('button[title="Toggle Sidebar"]')
        if toggle_button.count() > 0:
            print("Clicking sidebar collapse button...")
            toggle_button.click()
            time.sleep(1) # wait for animation
            
            # Save Collapsed Sidebar screenshot
            collapsed_path = r"C:\Users\ADMIN\.gemini\antigravity\brain\636ab0d7-eed3-45b0-8a2d-3d32e77c2a5c\sidebar_collapsed.png"
            page.screenshot(path=collapsed_path)
            print("Collapsed screenshot saved to " + collapsed_path)
        else:
            print("Toggle Sidebar button not found!")
            
        browser.close()

if __name__ == "__main__":
    run()
