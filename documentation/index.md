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


# How To Use

## Main Menues / Data Structure

### Branches

### Testruns

### Releases

### Compressors
