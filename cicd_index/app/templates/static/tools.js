function clicked_menu(id) {
    if (!id) {
        return;
    }
    window[id]();
}

function reload_table(table, params) {
    table.clearAll()
    var q = "?";
    if (params) {
        q += params;
    }
    table.load(table.config.url + q);
}

function reload_table_item($table, id, data) {
    if (!$table) return;
    var item = $table.getItem(id);
    if (!item) {
        return;
    }
    for (var key in data) {
        if (data.hasOwnProperty(key)) {
            item[key] = data[key];
        }
    }
    $table.updateItem(item.id, item);
}

function logout() {
    window.location = '/cicd/logout';
}

function users_admin() {
    location = '/cicd/user_admin';
}

function copyTextToClipboard(text) {
    window.prompt("Copy to clipboard: Ctrl+C, Enter", text);
  }