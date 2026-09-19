"""Set the isolated V7 production identity before app import."""

import os


os.environ["MYESTATEPICS_APPLICATION_NAME"] = "MyEstatePics AI Editor - V7.0"
os.environ["MYESTATEPICS_V7_PRODUCTION"] = "1"
