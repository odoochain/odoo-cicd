
function settings(){
    webix.ajax().get('/cicd/data/sites', {'name': current_details}).then(function(data) {
        data = data.json();
        var dumps_promise = webix.ajax().get("/cicd/possible_dumps");
        dumps_promise.then(function(dumps) {
            var dumps = dumps.json();
            var form = webix.ui({
                view: "window", 
                position: 'center',
                modal: true,
                head: "Settings",
                body: {
                    view: 'form',
                    width: 550,
                    complexData: true,
                    elements: [
                        { view: 'text', name: 'title', placeholder: "Title", },
                        { view: "textarea", name: 'note', placeholder: "Note..."},
                        { cols: [
                            { view: "label", label: "Dump"}, 
                            { view: "combo", name: 'dump', options: dumps, placeholder: "Dump" },
                        ]},
                        { cols: [
                            { view: "label", label: "Archived"}, 
                            { view: "checkbox", name: 'archive' },
                        ]},
                        { cols: [
                            { view: "label", label: "Dont anonymize / clear data"}, 
                            { view: "checkbox", name: 'keep_data' },
                        ]},
                        {
                            cols:[
                                { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                    var values = this.getParentView().getFormView().getValues();
                                    webix.ajax().post('/cicd/update/site', values).then(function(data) {
                                        form.hide();
                                        values = data.json();
                                        reload_table_item(
                                            $$("table-sites"),
                                            $$("table-sites").getSelectedItem()._id,
                                            values,
                                        )
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
            form.getChildViews()[1].setValues(data[0]);
            form.show();
        });
    });
    return false;
}