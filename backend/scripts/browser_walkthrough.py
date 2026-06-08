"""One-off Selenium walkthrough of the live frontend, driving the real upload ->
processing -> result flow against the live backend. Not part of the test suite —
a manual verification aid for todo "run backend live and walk through the
frontend in a browser end-to-end" (no interactive browser tool is available in
this environment, so this script substitutes for clicking through by hand).

Requires both dev servers running:
  backend:  uvicorn app.main:app --host 127.0.0.1 --port 8000
  frontend: npm run dev   (Vite, http://localhost:5173)
"""

from __future__ import annotations

import time
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

FRONTEND_URL = "http://localhost:5173/"
FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "synthetic_guitar.wav"
SHOTS_DIR = Path(__file__).resolve().parent / "walkthrough_shots"


def shot(driver, name: str) -> None:
    SHOTS_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOTS_DIR / f"{name}.png"
    driver.save_screenshot(str(path))
    print(f"  [screenshot] {path}")


def main() -> None:
    options = Options()
    options.binary_location = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1440,1000")
    driver = webdriver.Chrome(options=options)

    try:
        print("1. Loading home page...")
        driver.get(FRONTEND_URL)
        time.sleep(1.5)
        print("   title:", driver.title)
        heading = driver.find_element(By.TAG_NAME, "h1").text
        print("   heading:", heading)
        shot(driver, "1_home")

        print("2. Selecting a file via the dropzone's file input...")
        file_input = driver.find_element(By.CSS_SELECTOR, 'input[type="file"]')
        file_input.send_keys(str(FIXTURE))
        time.sleep(0.5)
        filename_text = driver.find_element(By.CSS_SELECTOR, "main").text
        assert "synthetic_guitar.wav" in filename_text, "selected filename not shown in UI"
        print("   file accepted, filename shown in UI")
        shot(driver, "2_file_selected")

        print("3. Inspecting settings panel controls...")
        main_text = driver.find_element(By.CSS_SELECTOR, "main").text
        for label in ("Строй гитары", "Каподастр", "Удобство аппликатуры", "Минимизировать переходы по грифу"):
            assert label in main_text, f"settings control {label!r} not visible"
        print("   settings controls visible:", "ok")

        print("4. Starting the job ('Построить табы')...")
        start_button = driver.find_element(By.XPATH, '//button[contains(text(), "Построить табы")]')
        start_button.click()
        time.sleep(1.5)
        shot(driver, "3_processing_start")

        print("5. Watching the processing timeline through to completion...")
        seen_stages = []
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            body_text = driver.find_element(By.CSS_SELECTOR, "main").text
            first_line = body_text.strip().splitlines()[0] if body_text.strip() else ""
            if first_line and (not seen_stages or seen_stages[-1] != first_line):
                seen_stages.append(first_line)
                print(f"   stage update: {first_line!r}")
            if "Готово" in body_text:
                break
            time.sleep(2)
        else:
            raise TimeoutError("job did not reach the result view within 240s")
        shot(driver, "4_result")

        print("6. Verifying the result view (tab viewer + audio player + download)...")
        result_text = driver.find_element(By.CSS_SELECTOR, "main").text
        assert "Готово" in result_text
        assert "Скачать .gp5" in result_text

        audio_elements = driver.find_elements(By.TAG_NAME, "audio")
        print(f"   <audio> elements present: {len(audio_elements)}")
        assert len(audio_elements) >= 1, "expected at least one <audio> element for playback"

        svg_elements = driver.find_elements(By.CSS_SELECTOR, "main svg")
        print(f"   tab viewer <svg> elements present: {len(svg_elements)}")
        assert svg_elements, "expected the tab viewer to render SVG notation"

        download_link = driver.find_element(By.XPATH, '//a[contains(text(), "Скачать .gp5")]')
        href = download_link.get_attribute("href")
        print("   download link href:", href)
        assert href and href.endswith(".gp5") or "/export.gp5" in href

        print("7. Clicking play on the audio player and confirming playback advances...")
        play_button = driver.find_element(By.CSS_SELECTOR, 'button[aria-label="Воспроизвести"]')
        before = driver.execute_script("return document.querySelector('audio').currentTime;")
        before_paused = driver.execute_script("return document.querySelector('audio').paused;")
        play_button.click()
        time.sleep(2.5)
        after = driver.execute_script("return document.querySelector('audio').currentTime;")
        after_paused = driver.execute_script("return document.querySelector('audio').paused;")
        print(f"   before: t={before:.2f}s paused={before_paused}  ->  after: t={after:.2f}s paused={after_paused}")
        assert before_paused is True, "expected audio to start paused"
        assert after_paused is False, "expected audio to be playing after clicking play"
        assert after > before, "expected playback position to advance"
        shot(driver, "5_playing")

        print("8. Pausing and confirming the playhead/seek-bar moved with playback...")
        play_button.click()
        time.sleep(0.3)
        paused_after_click = driver.execute_script("return document.querySelector('audio').paused;")
        assert paused_after_click is True, "expected the same button to pause playback"
        print("   pause toggled back: ok")

        print("\nWalkthrough completed successfully — full upload -> processing -> result flow verified live.")

    finally:
        driver.quit()


if __name__ == "__main__":
    main()
