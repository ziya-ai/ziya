import threading
import subprocess
import signal
import sys

from app.main import main


def frontend_start():
    subprocess.run(["npm", "run", "start"], cwd="frontend")


def frontend_install():
    subprocess.run(["npm", "install"], cwd="frontend")


def frontend_build():
    subprocess.run(["npm", "run", "build"], cwd="frontend")


def ziya():
    # frontend_build()
    main()


def signal_handler(sig, frame):
    print('Interrupt received, shutting down...')
    sys.exit(0)


def dev():
    frontend_thread = threading.Thread(target=frontend_start)
    signal.signal(signal.SIGINT, signal_handler)

    frontend_thread.start()
    try:
        print("Camme to main")
        main()
    except KeyboardInterrupt:
        print("Main process interrupted.")
    finally:
        frontend_thread.join()
