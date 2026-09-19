"""Set the isolated V7 pilot application identity before app import."""

import os


os.environ["MYESTATEPICS_APPLICATION_NAME"] = "MyEstatePics AI Editor - V7.0 Pilot"
os.environ["MYESTATEPICS_V7_PILOT"] = "1"
