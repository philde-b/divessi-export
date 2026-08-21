BASE_URL = "https://api.divessi.com/app/a21.php"
CLIENT_APP = "0815_ADR"

REQUEST_TIMEOUT = 30

# The server rate limits by going silent: after a run of requests it stops
# answering for several minutes (observed: well over five), and the quota
# looks like a rolling window rather than a burst cap. Keep a polite gap
# between calls and, when it does go quiet, wait it out once.
DEFAULT_DELAY = 2.0
DISCOVERY_COOLDOWN = 300

TANK_TYPES = {19: "Steel", 20: "Aluminium"}

# Commands confirmed to return data (checked against the live API on
# 2026-08-21). These are fetched on every run.
KNOWN_COMMANDS = (
    "get_divelog",
    "get_user_data",
    "get_divesites",
)

# The API is a single dispatcher endpoint driven by a "what" parameter and
# has no public documentation, so --discover probes guesses on top of the
# known commands. An unknown name costs one fast 404.
#
# Already probed and answered 404, do not bother re-adding: get_logbook,
# get_logbook_details, get_profile, get_user, get_userdata, get_account,
# get_settings, get_certifications, get_certificates, get_cards,
# get_digital_cards, get_trainings, get_training, get_courses,
# get_progress, get_specialties, get_recognitions, get_awards,
# get_achievements, get_dive_sites.
DISCOVERY_CANDIDATES = KNOWN_COMMANDS + (
    # not yet reached in the first sweep
    "get_sites",
    "get_dive_centers",
    "get_divecenters",
    "get_centers",
    "get_shops",
    "get_gear",
    "get_equipment",
    "get_media",
    "get_images",
    "get_photos",
    "get_buddies",
    "get_friends",
    "get_wildlife",
    "get_species",
    "get_statistics",
    "get_stats",
    "get_events",
    "get_news",
    "get_notifications",
    "get_messages",
    "get_orders",
    "get_products",
    "get_instructor",
    "get_pro",
    "get_versions",
    "get_config",
    # modelled on the names that are known to work
    "get_user_certifications",
    "get_user_certs",
    "get_user_cards",
    "get_user_profile",
    "get_user_settings",
    "get_user_logbook",
    "get_user_divelog",
    "get_user_media",
    "get_user_photos",
    "get_user_buddies",
    "get_user_gear",
    "get_user_stats",
    "get_user_trainings",
    "get_user_courses",
    "get_divelog_details",
    "get_divelog_media",
    "get_divelog_stats",
    "get_divesite",
    "get_divecenter",
    "get_certs",
)

MEDIA_EXTENSIONS = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".heic",
    ".mp4",
    ".mov",
    ".pdf",
)

MEDIA_ALLOWED_HOST_SUFFIX = "divessi.com"
