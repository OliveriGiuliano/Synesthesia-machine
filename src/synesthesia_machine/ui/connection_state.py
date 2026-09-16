"""Qt-free connection UI-state keys shared across the editor.

These keys index the ``ui_state`` mapping on a graph connection. They live in a
Qt-free module so headless policy (for example the demand-root computation in
:mod:`synesthesia_machine.ui.demand_roots`) can depend on them without importing
the Qt command modules.
"""

#: Key recording whether a connection's preview pill is visible. An absent key
#: (or a truthy value) means the pill is shown; ``False`` hides it.
PREVIEW_VISIBLE_KEY = "preview_visible"
