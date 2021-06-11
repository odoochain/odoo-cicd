function fetch_dump() {
    debugger;
    var dumps_promise = webix.ajax().get("/cicd/possible_input_dumps");
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
                    { view: "label", label: "Dump" }, 
                    { view: "combo", name: 'dump', options: dumps },
                    {
                        'cols': [
                            { view: "label", label: "Anonymize" },
                            { view: "checkbox", name: 'anonymize', value: 1 },
                        ]
                    },
                    {
                        'cols': [
                            { view: "label", label: "Erase Data (make small)" },
                            { view: "checkbox", name: 'erase', value: 1 },
                        ]
                    },
                    {
                        cols:[
                            { view:"button", value:"OK", css:"webix_primary", click: function() { 
                                    var values = this.getParentView().getFormView().getValues();
                                    webix.ajax().get('/cicd/transform_input_dump', {
                                        'dump': values.dump,
                                        'erase': values.erase,
                                        'anonymize': values.anonymize,
                                    }).then(function(data) {
                                        form.hide();
                                        window.open(data.json().live_url);
                                    }).fail(function(response) {
                                        alert(response.statusText);
                                        console.error(response.responseText);
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
        form.show();
    });
}