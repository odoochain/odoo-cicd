# Odoo CI/CD - Continuous Integration Continous Deployment

## Introduction

Why is there a customized Application for odoo deployment next to github-actions,
Jenkins, gitlab-runner, bamboo and others?

为什么在github操作、Jenkins、gitlab runner、bamboo和其他应用程序旁边有一个定制的odoo部署应用程序？

Some considerations(注意事项):

* Usually you setup a pipeline for your project. If you dont do many projects but just some you probably miss a reusable template. If you have several projects then projects usually deviate if there is no structure from the beginning
* Intelligent Speedrun of unittests: If only one character is changed, this can break the complete system. But usually - not (depends...)
In the cicd a smart algorithm is integrated to analyze dependencies of modules, python version and others, so that on a change only relevant unittests are executed --> saves up to 95% of time per testrun. In a default cicd setup manually in jenkins or github, usually everything is always tested.
* Useful tools included: per click you get an anonymized database
* Browsable branches: remarkable is here, that you dont to deal arround with DNS servers at your company. 
A special cookie is used to redirect traffic to the desired instance. Odoo is not runnable behind a path-rewrite.

通常为项目设置管道。如果你做的项目不多，但只做了一些，你可能会错过一个可重用的模板。

如果您有多个项目，那么如果从一开始就没有结构，项目通常会偏离单元测试的智能快速运行：如果只更改一个字符，则可能会破坏整个系统。但通常不是（取决于…）

在cicd中，集成了一个智能算法来分析模块、python版本和其他版本的依赖性，因此在发生更改时，只执行相关的单元测试-->每次测试运行可节省多达95%的时间。

在jenkins或github中手动设置默认cicd时，通常都会测试所有内容。有用的工具包括：每点击一次，你就会得到一个匿名数据库

可浏览的分支：值得注意的是这里，你不需要处理你公司的DNS服务器。 一个特殊的cookie用于将流量重定向到所需的实例。在路径重写之后，Odoo无法运行。

# How To Use

## Main Menues / Data Structure

### Branches

### Testruns

### Releases

### Compressors
