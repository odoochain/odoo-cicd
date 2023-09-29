# Odoo CI/CD - Continuous Integration Continous Deployment

## Introduction

Why is there a customized Application for odoo deployment next to github-actions,
Jenkins, gitlab-runner, bamboo and others?

Some considerations:

* Usually you setup a pipeline for your project. If you dont do many projects but just some you probably miss a reusable template. If you have several projects then projects usually deviate if there is no structure from the beginning
* Intelligent Speedrun of unittests: If only one character is changed, this can break the complete system. But usually - not (depends...)
In the cicd a smart algorithm is integrated to analyze dependencies of modules, python version and others, so that on a change only relevant unittests are executed --> saves up to 95% of time per testrun. In a default cicd setup manually in jenkins or github, usually everything is always tested.
* Useful tools included: per click you get an anonymized database
* Browsable branches: remarkable is here, that you dont to deal arround with DNS servers at your company. A special cookie is used to redirect traffic to the desired instance. Odoo is not runnable behind a path-rewrite.

## 中文介紹-注意事項

* 通常您为项目设置管道。如果你不做很多项目，而只是一些，你可能会错过一个可重用的模板。如果你有几个项目，那么如果从一开始就没有结构，项目通常会偏离

* 单元测试的智能速度运行：如果只更改了一个字符，这可能会破坏整个系统。但通常 - 不是（取决于...)

在cicd中，集成了智能算法来分析模块，python版本和其他的依赖关系，因此在更改时仅执行相关的单元测试 - >每次测试可节省高达95%的时间。在詹金斯或github中手动设置的默认cicd中，通常所有内容都始终经过测试。

* 包括有用的工具：每次点击您都会获得一个匿名数据库
14
* 可浏览的分支机构：值得注意的是，您不会与公司的DNS服务器打交道。特殊 Cookie 用于将流量重定向到所需的实例。Odoo不能在路径重写后面运行。

# How To Use

## Main Menues / Data Structure

### Branches

### Testruns

### Releases

### Compressors
