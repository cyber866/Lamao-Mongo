#
# A simple script to automatically update the yt-dlp library.
# This helps resolve issues when website extractors break.
#

import subprocess
import sys
import logging

log = logging.getLogger("updater")

def update_yt_dlp():
    """
    Runs the pip command to update yt-dlp to the latest version.
    """
    try:
        log.info("Checking for yt-dlp updates...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"])
        log.info("✅ yt-dlp updated successfully!")
    except subprocess.CalledProcessError as e:
        log.error(f"Failed to update yt-dlp: {e}")
    except Exception as e:
        log.error(f"An unexpected error occurred during update: {e}")

if __name__ == "__main__":
    update_yt_dlp()

