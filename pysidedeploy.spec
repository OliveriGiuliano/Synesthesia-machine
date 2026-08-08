[app]
title = Synesthesia Machine
project_dir = src/synesthesia_machine
input_file = SynesthesiaMachine.py
exec_directory = packaging/out
project_file =
icon = src/synesthesia_machine/resources/synesthesia-machine.ico

[python]
python_path =
packages = Nuitka==4.1

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = iconengines,imageformats,platforms,styles

[nuitka]
mode = standalone
extra_args = --assume-yes-for-downloads --windows-console-mode=disable --company-name="Synesthesia Machine" --product-name="Synesthesia Machine" --file-description="Visual video-to-MIDI instrument" --file-version=0.1.0.0 --product-version=0.1.0 --copyright="Copyright (c) 2026 Synesthesia Machine contributors" --include-package=synesthesia_machine --include-package=mido.backends --include-package=rtmidi --include-package=_sounddevice_data --include-data-files=src/synesthesia_machine/resources/synesthesia-machine.ico=synesthesia_machine/resources/synesthesia-machine.ico --noinclude-qt-translations

[android]
wheel_pyside =
wheel_shiboken =
recipe_dir =
jars_dir =
ndk_path =
sdk_path =
