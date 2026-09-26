[app]
title = MikrotikBF
package.name = mikrotikbf
package.domain = com.hussein

source.dir = .
source.include_exts = py,png,jpg,kv,atlas
source.include_patterns = assets/*,images/*
source.exclude_patterns = .buildozer,bin,__pycache__,*.pyc,*.pyo,.git,.github

version = 2.0.0

requirements = python3,kivy,requests,urllib3,certifi,chardet,idna,beautifulsoup4,soupsieve,colorama,charset-normalizer

orientation = portrait
fullscreen = 0
android.api = 33
android.minapi = 21
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = 1
android.permissions = INTERNET,ACCESS_NETWORK_STATE,WRITE_EXTERNAL_STORAGE,READ_EXTERNAL_STORAGE
android.wakelock = 1
android.accept_sdk_license = True

[buildozer]
log_level = 2
warn_on_root = 1
