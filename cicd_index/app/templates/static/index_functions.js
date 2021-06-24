var update_live_values = null;
var update_resources = null;

function update_odoo_configuration(e) {
    var sitename = current_details;
    debugger;
    debugger;
    debugger;
    debugger;
}

function clear_webassets(sitename) {
    var sitename = sitename || current_details;
    var url = "/cicd/clear_webassets?name=" + sitename;
    webix.ajax().get(url).then(function(res) {
        webix.message("Cleared Webassets " + sitename);
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });

}

function reload_restart(sitename) {
    var sitename = sitename || current_details;
    var url = "/cicd/reload_restart?name=" + sitename;
    webix.ajax().get(url).then(function(res) {
        webix.message("Reloading and restarting triggered for " + sitename);
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });

}

function build_again() {
    _build_again(false);
}

function build_again_all() {
    _build_again(true);
}

function turn_into_dev() {
    var sitename = current_details;
    var url = "/cicd/turn_into_dev?site=" + sitename;
    webix.ajax().get(url).then(function(res) {
        webix.message("Turned into dev: " + sitename, "info");
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}

function delete_instance(name) {
    webix.message("Deleting in Background " + name, "info");
    webix.ajax().get('/cicd/delete', {
        'name': name,
    }).then(function(data) {
        webix.message("Instance erased: " + current_details, "info");
    }).fail(function(data) {
        alert(data.statusText);
        console.error(data.responseText);
    });
}

function show_logs(service_name) {
    window.open("/cicd/show_logs?name=" + current_details + "&service=" + service_name);
}

function shell() {
    window.open("/cicd/shell_instance?name=" + current_details);
}

function debug() {
    window.open("/cicd/debug_instance?name=" + current_details);
}

function show_mails() {
    window.open("/cicd/start?initial_path=/mailer/&name=" + current_details);
}

function start_instance() {
    window.open("/cicd/start?name=" + current_details);
}

function build_log() {
    window.open("/cicd/build_log?name=" + current_details);
}

function delete_unused() {
    if (!confirm("Continue? Unused databased will be cleared.")) {
        return;
    }
    webix.message('Cleaning');
    webix.ajax().get("/cicd/cleanup").then(function(res) {
        webix.message("Cleanup done", "info");
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}


function _build_again(do_all) {
    var url = "/cicd/build_again"
    if (do_all) {
        do_all = '1';
    } else {
        do_all = '0'
    }

    webix.ajax().get("/cicd/build_again?all=" + do_all + "&name=" + current_details).then(function(res) {
        webix.message("Triggered rebuild", "info");
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}

function update_sites() {
    var archive = $$('show_archive').getValue();
    var url = "archive=" + archive;
    reload_table($$('table-sites'), url);
}

function restart_delegator() {
    webix.message("Restarting delegator", "info");
    var url = "/cicd/restart_delegator"
    webix.ajax().get(url).then(function(res) {
        webix.message("Restarted delegator", "info");
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}

function show_cicd_app_logs() {
    window.location = '/cicd/logs';
}

function start_all() {
    webix.message('Starting all docker containers; also restarting delegator after that.');
    webix.ajax().get("/cicd/start_all").then(function(res) {
        webix.message("Started all instances possible");
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}

function restart() {
    var site = current_details;
    webix.message('Restarting docker containers of' + current_details + '. Reporting immediately when done.');
    webix.ajax().get("/cicd/restart_docker?name=" + current_details).then(function(res) {
        webix.message("Restarted " + site);
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}
function rebuild() {
    show_reset_form(current_details);
}
function reload_details(name) {
    var template = $$('webix-instance-details');
    webix.ajax().get('/cicd/data/sites?name=' + name).then(function(data) {
        template.setValues(data.json()[0]);
        template.show();
        $$('site-toolbar').show();
        current_details = name;
    }).fail(function(response) {
        webix.message("Error: " + response.statusText, "error");
    });
}

update_live_values = function() {

    webix.ajax().get('/cicd/data/site/live_values').then(function(res) {
        try {
            var data = res.json();
            var $table = $$("table-sites")
            for (i = 0; i < data.sites.length; i++) {
                var record = data.sites[i];
                reload_table_item($table, record._id, record);
            }
        } catch(e) {
            console.error(e);
        }
        setTimeout(update_live_values, 3000);
    });


}

update_resources = function() {

    webix.ajax().get('/cicd/get_resources').then(function(res) {
        var $template = $$('resources-view');
        $("div#resources").html($(res.text()));
        setTimeout(update_resources, 30000);
    });

}

function show_robot_results() {
    window.open("/cicd/start?initial_path=/robot-output&name=" + current_details);
}

function run_robot_tests() {
    webix.ajax().get('/cicd/run_robot_tests?site' + current_details).then(function(res) {
        webix.message("Tests started. You can view by click 'Build Log'.");
    });
}