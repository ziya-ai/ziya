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
    import sys
    symbols = "--symbols" in sys.argv
    env = os.environ.copy() if symbols else None
    if symbols:
        env["GENERATE_SOURCEMAP"] = "true"
        print("🗺️  Building with source maps enabled...")
    subprocess.run(["npm", "run", "build"], cwd="frontend", env=env)
    if symbols:
        _deploy_sourcemaps()


def _deploy_sourcemaps():
    """Copy built JS files + source maps to installed package location."""
    import glob
    import shutil
    import site

    build_js = os.path.join("frontend", "build", "static", "js")
    if not os.path.exists(build_js):
        print("❌ No build output found at frontend/build/static/js")
        return

    # Find installed package location
    for site_dir in site.getsitepackages() + [site.getusersitepackages()]:
        installed_js = os.path.join(site_dir, "app", "templates", "static", "js")
        if os.path.exists(installed_js):
            for f in glob.glob(os.path.join(build_js, "main.*")):
                dest = os.path.join(installed_js, os.path.basename(f))
                shutil.copy2(f, dest)
                print(f"✅ Deployed: {os.path.basename(f)}")
            print("🗺️  Source maps deployed to installed package")
            return
    print("⚠️  Could not find installed package location — copy manually:")
    print(f"   cp frontend/build/static/js/main.* <site-packages>/app/templates/static/js/")


def ziya():
    # Check installation before anything else
    _check_installation()
    
    # Check for version flag first
    if "--version" in sys.argv:
        # Fast version check without plugin initialization
        import os
        try:
            from .utils.version_util import get_current_version
        except ImportError:
            from app.utils.version_util import get_current_version
        version = get_current_version()
        edition = os.environ.get('ZIYA_EDITION', 'Community Edition')
        print(f"Ziya version {version} - {edition}")
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
