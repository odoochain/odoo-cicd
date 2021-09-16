{% include "static/tools.js" %}

// start live valuen
var current_details = null;


{% include "static/index_functions.js" %}
{% include "static/index_backupdb_form.js" %}
{% include "static/index_delete_instance_form.js" %}
{% include "static/index_make_new_instance_form.js" %}
{% include "static/index_appsettings.js" %}
{% include "static/index_reset_form.js" %}
{% include "static/index_process_input_dump.js" %}
{% include "static/index_ui.js" %}

setTimeout(update_live_values, 2000);
setTimeout(update_resources, 2000);
