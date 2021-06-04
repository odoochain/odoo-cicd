{
    id: "webix-instance-details",
    maxWidth: 650,
    css: "webix_dark",
    view: "form",
    type: "body",
    scroll: true,
    hidden: true,
    on: {
        'onChange': function() {
            var sitename = current_details;
            var url = "/cicd/update/site?name=" + sitename;
            var values = this.getValues();
            var values_store = {
                "_id": values._id,
                'note': values.note,
                'odoo_settings': values.odoo_settings,
                'archived': values.archived == '1',
                'dump': values.dump,
                'docker_no_cache': values.docker_no_cache == '1',
                'do_backup_regularly': values.do_backup_regularly == '1',
            }
            webix.ajax().post(url, values_store).then(function(res) {
            }).fail(function(response) {
                webix.message("Error: " + response.statusText, "error");
            });
        }
    },
    complexData: true,
    elements: [
        {
            cols: [
                { view: "label", label: "Name"}, 
                { view: 'text', name: 'name', readonly: true},
            ],
        },
        {
            cols: [
                { view: "label", label: "Date registered"}, 
                { view: 'datepicker', name: 'date_registered', readonly: true, timepicker: true},
            ],
        },
        {
            cols: [
                { view: "label", label: "Updated"}, 
                { view: 'datepicker', name: 'updated', readonly: true, timepicker: true},
            ],
        },
        {
            cols: [
                { view: "label", label: "Author"}, 
                { view: 'text', name: 'git_authored_date', readonly: true},
            ],
        },
        {
            cols: [
                { view: "label", label: "SHA"}, 
                { view: 'text', name: 'git_authored_date', readonly: true},
            ],
        },
        {
            cols: [
                { view: "label", label: "Based on"}, 
                { view: 'text', name: 'dump_name', readonly: true},
            ]
        },
        {
            cols: [
                { view: "label", label: ""}, 
                { view: 'datepicker', name: 'dump_date', readonly: true, timepicker: true},
            ]
        },
        { cols: [
            { view: "label", label: "Choose Dump"}, 
            { view: "combo", name: 'dump', options: '/cicd/possible_dumps', placeholder: "Dump" },
        ]},
        {
            cols: [
                { view: "label", label: "Archived"}, 
                { view: 'checkbox', name: 'archived' },
            ]
        },
        {
            cols: [
                { view: "label", label: "Backup regulary"}, 
                { view: 'checkbox', name: 'do_backup_regularly' },
            ]
        },
        {
            cols: [
                { view: "label", label: "No cache at next build"}, 
                { view: 'checkbox', name: 'docker_no_cache' },
            ]
        },
        { view: "label", label: "Description"}, 
        { view: 'textarea', name: 'git_desc', readonly: true, height: 80},
        { view: "label", label: "Note"}, 
        { view: 'textarea', height: 150, name: 'note', },
        { view: "label", label: "Odoo Settings"}, 
        { view: 'textarea', height: 150, name: 'odoo_settings', },
    ],
}