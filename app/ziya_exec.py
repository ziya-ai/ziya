import threading
import subprocess
import signal
import sys
import os


def frontend_start():
    subprocess.run(["npm", "run", "start"], cwd="frontend")


def frontend_install():
    subprocess.run(["npm", "install"], cwd="frontend")


def frontend_build():
    subprocess.run(["npm", "run", "build"], cwd="frontend")


def ziya():
    # Check for version flag first to avoid importing main
    if "--version" in sys.argv:
        # Use direct import of version utility to avoid loading the full app
        try:
            from .utils.version_util import get_current_version
        except ImportError:
            # Fallback for development mode
            from app.utils.version_util import get_current_version
        print(f"Ziya version {get_current_version()}")
        return
        
    # Only import main when needed
    try:
        from .main import main
    except ImportError:
        # Fallback for development mode
        from app.main import main
    main()


def signal_handler(sig, frame):
    print('Interrupt received, shutting down...')
    sys.exit(0)


def dev():
    frontend_thread = threading.Thread(target=frontend_start)
    signal.signal(signal.SIGINT, signal_handler)

    frontend_thread.start()
    try:
        # Only import main when needed
        try:
            from .main import main
        except ImportError:
            # Fallback for development mode
            from app.main import main
        print("Came to main")
        main()
    except KeyboardInterrupt:
        print("Main process interrupted.")
    finally:
        frontend_thread.join()
