function appsettings(){
    webix.ajax().get('/cicd/data/app_settings', {}).then(function(data) {
        data = data.json();
        var form = webix.ui({
            view: "window", 
            position: 'center',
            modal: true,
            head: "Settings",
            width: 550,
            body: {
                view: 'form',
                complexData: true,
                elements: [
                    { cols: [
                        { view: "label", label: "Dont update translations"}, 
                        { view: "checkbox", name: 'no_i18n' },
                    ]},

                    { view: "label", label: "Concurrent Builds"}, 
                    { view: 'text', name: 'concurrent_builds' },

                    { view: "label", label: "Default Merge Destination"}, 
                    { view: 'text', name: 'default_merge_target' },

                    { cols: [
                        { view: "label", label: "Auto Create New Branches"}, 
                        { view: 'checkbox', name: 'auto_create_new_branches' },
                    ]},

                    { view: "label", label: "Odoo Settings"}, 
                    { view: 'textarea', height: 150, name: 'odoo_settings', },
                    {
                        cols:[
                            { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                var values = this.getParentView().getFormView().getValues();
                                webix.ajax().post('/cicd/data/app_settings', values).then(function(data) {
                                    form.hide();
                                    values = data.json();
                                });
                                }
                            },
                            { view:"button", value:"Cancel", click: function() {
                                form.hide();
                            }}
                        ]
                    }
                ],
                on: {
                    'onSubmit': function() {
                    },
                }
            }
        });
        form.getChildViews()[1].setValues(data);
        form.show();
    });
    return false;
}
