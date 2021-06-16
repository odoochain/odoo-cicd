{% include "static/tools.js" %}

// var menu = {
//     view: "menu",
//     batch: "admin",
//     autowidth: true,
//     width: 120,
//     type: {
//         subsign: true,
//     },
//     data: [
//         {
//             id: "settings_mainmenu",
//             view: "menu",
//             value: "Admin...",
//             config: { on: { onItemClick: clicked_menu}},
//             submenu: [
//                 { $template:"Separator" },
//                 { view:"button", id:"return", value:"Return"},
//             ]
//         },
//     ],
// }

webix.ready(function() {
    webix.ajax().get('/cicd/start_info').then(function(startinfo) {
        startinfo = startinfo.json();
		webix.ui({
			type: 'wide',
			cols: [{
				rows: [
						{
							view: "template",
							type: "header",
							css: "webix_dark",
							template: "Tasks"
						},
						{
							view: 'toolbar',
							css: "webix_dark",
							id: 'site-toolbar-common',
							elements: [
								{ view:"button", id:"return_to_branches", value:"Return", click: clicked_menu},
							],
						},
						{
							id: 'table-logs',
							view: "datatable",
							navigation: true,
							headerRowHeight: 60,
							rowHeight: 40,
							select: 'row',
							autoConfig: false,
							url: '/cicd/logs/data',
							editable: false,
							data: [],
							leftSplit: 0,
							scrollX: false,
							on: {
								onItemClick: function(id, e, trg) {
									var name = this.getSelectedItem().name;
									window.open("/cicd/live_log?name=" + name);
								},
							},
							columns:[
								{ id: 'name', header: 'Filename', minWidth: 250},
								{ id: 'date', header: 'Name', minWidth: 450},
								{ id: 'title', header: 'Name', minWidth: 450},
							],
						},
					],
			}],
		});


		webix.ui.fullScreen();

		if (!startinfo.is_admin) {
		}

	});
});