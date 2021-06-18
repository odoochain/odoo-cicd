
function show_reset_form(name) {
    var dumps_promise = webix.ajax().get("/cicd/possible_dumps");
    dumps_promise.then(function(dumps) {
        dumps = dumps.json();
        var form = webix.ui({
            view: "window", 
            position: 'center',
            modal: true,
            head: "Reset Instance",
            width: 550,
            body: {
                view: 'form',
                complexData: true,
                elements: [
                    { view: "label", label: "Dump", id: 'form_reset.label_dump'}, 
                    { view: "combo", name: 'dump', options: dumps, id: 'form_reset.dump' },
                    {
                        'cols': [
                            { view: "label", label: "Make Instance from scratch" },
                            { view: "checkbox", name: 'no_module_update', on: {
                                onChange: function(newValue, oldValue, config) {
                                    if (newValue == 1) {
                                        $$("form_reset.dump").hide();
                                        $$("form_reset.label_dump").hide();
                                    }
                                    else {
                                        $$("form_reset.dump").show();
                                        $$("form_reset.label_dump").show();
                                    }
                                }
                            }
                            },
                        ]
                    },
                    {
                        'cols': [
                            { view: "label", label: "Docker Build: No Cache" },
                            { view: "checkbox", name: 'no_cache' },
                        ]
                    },
                    { view: 'label', label: "Rebuilding the instance in background. Will take some time, until instance is up again."},
                    {
                        cols:[
                            { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                    var values = this.getParentView().getFormView().getValues();
                                    webix.ajax().get('/cicd/trigger/rebuild', {
                                        'name': name,
                                        'dump': values.dump,
                                        'no_cache': values.no_cache,
                                        'no_module_update': values.no_module_update,
                                    }).then(function(data) {
                                        form.close();
                                    }).fail(function(response) {
                                        alert(response.statusText);
                                        console.error(response.responseText);
                                    });
                                    }
                            },
                            { view:"button", value:"Cancel", click: function() {
                                form.close();
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
        form.show();
    });
}