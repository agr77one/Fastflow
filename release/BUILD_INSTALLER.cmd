@echo off
:: Double-click this file to build the Flowkey installer.
:: All the real work lives in installer\bootstrap.cmd.
call "%~dp0installer\bootstrap.cmd" %*
