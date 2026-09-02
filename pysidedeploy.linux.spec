[app]
title = Synesthesia Machine
project_dir = src/synesthesia_machine
input_file = SynesthesiaMachine.py
exec_directory = packaging/out
project_file =
icon = src/synesthesia_machine/resources/app-icon-master.png

[python]
python_path =
packages = Nuitka==4.1.3

[qt]
qml_files =
excluded_qml_plugins =
modules = Core,Gui,Widgets
plugins = iconengines,imageformats,platforms,styles

[nuitka]
mode = standalone
extra_args = --assume-yes-for-downloads --include-package=synesthesia_machine --include-module=av.utils --include-package=mido.backends --include-package=rtmidi --include-data-files=src/synesthesia_machine/resources/app-icon-master.png=synesthesia_machine/resources/app-icon-master.png --no-prefer-source-code --noinclude-qt-translations
