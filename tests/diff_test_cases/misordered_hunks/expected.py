def get_current_settings():
    """Get current settings from environment."""
    settings = {}
    # Get settings with defaults
    logger.info("Retrieving current settings")
    settings['temperature'] = os.environ.get("TEMPERATURE", "0.3")
    settings['top_k'] = os.environ.get("TOP_K", "15")
    return settings

def update_settings(new_settings):
    """Update environment settings."""
    try:
        # Log the requested changes
        logger.info("Updating settings:")
        logger.info(f"  Temperature: {new_settings.temperature}")
        logger.info(f"  Top K: {new_settings.top_k}")

        os.environ["TEMPERATURE"] = str(new_settings.temperature)
        os.environ["TOP_K"] = str(new_settings.top_k)
        logger.info("Settings successfully updated")
        return True
    except Exception as e:
        logger.error(f"Error: {e}")
        return False

def validate_settings(settings):
    """Validate settings values."""
    if not 0 <= float(settings.temperature) <= 1:
        logger.error(f"Invalid temperature: {settings.temperature}")
        return False
    if not 0 <= int(settings.top_k) <= 100:
        logger.error(f"Invalid top_k: {settings.top_k}")
        return False
    logger.info("Settings validation successful")
    return True
