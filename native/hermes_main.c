#include <Python.h>
#include <stdlib.h>

static char **build_argv(int argc, char **argv) {
    char **result = calloc((size_t)argc + 1, sizeof(*result));
    if (result == NULL) {
        return NULL;
    }
    for (int i = 0; i < argc; ++i) {
        result[i] = argv[i];
    }
    return result;
}

int main(int argc, char **argv) {
    char **python_argv = build_argv(argc, argv);
    if (python_argv == NULL) {
        fputs("hermes: unable to allocate argument vector\n", stderr);
        return 70;
    }

    /* The runtime archive is the only external payload.  Configure the
       embedded interpreter explicitly because iOS has no host-style prefix. */
    PyConfig config;
    PyConfig_InitIsolatedConfig(&config);
    config.parse_argv = 0;

    PyStatus status = PyConfig_SetBytesArgv(&config, argc, python_argv);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    status = PyConfig_SetString(&config, &config.program_name, L"./hermes");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    status = PyConfig_SetString(&config, &config.run_module,
                                L"hermes_cli.main");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    status = PyWideStringList_Append(&config.module_search_paths,
                                     L"./hermesrt.zip");
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    config.module_search_paths_set = 1;

    status = Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) {
        PyConfig_Clear(&config);
        free(python_argv);
        Py_ExitStatusException(status);
    }
    int result = PyRun_SimpleString(
        "import runpy\n"
        "runpy.run_module('hermes_cli.main', run_name='__main__')\n");
    if (result != 0) {
        PyErr_Print();
    }
    PyConfig_Clear(&config);
    free(python_argv);
    return result;
}
