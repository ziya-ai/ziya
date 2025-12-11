import os
import signal
import subprocess
import sys
import threading


def _check_installation():
    """Check if ziya is properly installed and accessible."""
    # Check Python version
    if sys.version_info < (3, 10):
        print(f"❌ Ziya requires Python 3.10 or higher")
        print(f"   Current: Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        print(f"\n   Fix: python3.10 -m pip install --user ziya")
        sys.exit(1)
    
    # Check if running from correct location
    install_path = os.path.dirname(os.path.abspath(__file__))
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    
    # Warn if version mismatch detected
    if f"python3.9" in install_path and sys.version_info >= (3, 10):
        print(f"⚠️  Installation mismatch detected")
        print(f"   Ziya installed for Python 3.9 but running with Python {py_version}")
        print(f"\n   Fix: python{py_version} -m pip install --user --force-reinstall ziya\n")
        sys.exit(1)


def frontend_start():
    subprocess.run(["npm", "run", "start"], cwd="frontend")


def frontend_install():
    subprocess.run(["npm", "install"], cwd="frontend")


def frontend_build():
    subprocess.run(["npm", "run", "build"], cwd="frontend")


def ziya():
    # Check installation before anything else
    _check_installation()
    
    # Check for version flag first
    if "--version" in sys.argv:
        # Initialize plugins to get branding
        try:
            from .plugins import initialize
            initialize()
        except ImportError:
            from app.plugins import initialize
            initialize()
        
        # Now print version with branding
        try:
            from .main import print_version
        except ImportError:
            from app.main import print_version
        print_version()
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
