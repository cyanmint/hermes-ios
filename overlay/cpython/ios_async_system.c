/* HermesLink iOS asynchronous ios_system process adapter. */
#include <Python.h>

#if defined(__APPLE__)
#  include <TargetConditionals.h>
#endif

#if !defined(TARGET_OS_IPHONE) || !TARGET_OS_IPHONE
#  error "_hermesios is an iOS-only module"
#endif

#include <dlfcn.h>
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#define IOS_TASK_CAPACITY 32
#define IOS_CANCEL_GRACE_MS 1500

typedef struct {
    int used;
    int done;
    int cancelled;
    int close_requested;
    int condition_initialized;
    int starting;
    unsigned int waiters;
    unsigned long long id;
    pid_t ios_pid;
    int status;
    char *command;
    FILE *writer;
    pthread_t worker;
    pthread_cond_t changed;
} ios_task;

static ios_task tasks[IOS_TASK_CAPACITY];
static pthread_mutex_t tasks_mutex = PTHREAD_MUTEX_INITIALIZER;
static unsigned long long next_task_id = 1;
/* ios_closeSession removes the map entry but does not free its sessionParameters. */
static const char runner_session_id[] = "hermes-ios-runner";

static void *ios_symbol(const char *name)
{
    return dlsym(RTLD_DEFAULT, name);
}

static ios_task *find_task_locked(unsigned long long id)
{
    for (size_t index = 0; index < IOS_TASK_CAPACITY; ++index) {
        if (tasks[index].used && tasks[index].id == id) {
            return &tasks[index];
        }
    }
    return NULL;
}

static void clear_task_locked(ios_task *task)
{
    free(task->command);
    task->command = NULL;
    task->writer = NULL;
    task->used = 0;
    task->done = 0;
    task->cancelled = 0;
    task->close_requested = 0;
    task->waiters = 0;
    task->starting = 0;
    task->ios_pid = 0;
    task->status = 0;
    task->id = 0;
}

static void maybe_clear_task_locked(ios_task *task)
{
    if (task->used && task->done && task->close_requested && task->waiters == 0) {
        clear_task_locked(task);
    }
}

static void write_runner_error(ios_task *task, const char *message)
{
    if (task->writer != NULL) {
        fprintf(task->writer, "ios_system runner: %s\n", message);
        fflush(task->writer);
    }
}

static void *run_ios_task(void *opaque)
{
    ios_task *task = (ios_task *)opaque;
    typedef void (*switch_session_fn)(const void *);
    typedef void (*set_streams_fn)(FILE *, FILE *, FILE *);
    typedef FILE *(*stream_fn)(void);
    typedef pid_t (*fork_fn)(void);
    typedef int (*system_fn)(const char *);
    typedef void (*waitpid_fn)(pid_t);
    typedef void (*release_pid_fn)(pid_t);
    typedef void *(*pool_push_fn)(void);
    typedef void (*pool_pop_fn)(void *);

    switch_session_fn switch_session = (switch_session_fn)ios_symbol("ios_switchSession");
    set_streams_fn set_streams = (set_streams_fn)ios_symbol("ios_setStreams");
    stream_fn get_stdin = (stream_fn)ios_symbol("ios_stdin");
    stream_fn get_stdout = (stream_fn)ios_symbol("ios_stdout");
    stream_fn get_stderr = (stream_fn)ios_symbol("ios_stderr");
    fork_fn ios_fork = (fork_fn)ios_symbol("ios_fork");
    system_fn ios_system = (system_fn)ios_symbol("ios_system");
    waitpid_fn ios_waitpid = (waitpid_fn)ios_symbol("ios_waitpid");
    release_pid_fn release_pid = (release_pid_fn)ios_symbol("ios_releaseThreadId");
    pool_push_fn pool_push = (pool_push_fn)ios_symbol("objc_autoreleasePoolPush");
    pool_pop_fn pool_pop = (pool_pop_fn)ios_symbol("objc_autoreleasePoolPop");
    void *pool = pool_push != NULL ? pool_push() : NULL;
    int status = 127;
    FILE *saved_stdin = NULL;
    FILE *saved_stdout = NULL;
    FILE *saved_stderr = NULL;
    FILE *command_writer = NULL;
    int command_stream_transferred = 0;

    if (switch_session == NULL || set_streams == NULL ||
        get_stdin == NULL || get_stdout == NULL || get_stderr == NULL ||
        ios_fork == NULL || ios_system == NULL || ios_waitpid == NULL ||
        release_pid == NULL) {
        write_runner_error(task, "required ios_system symbols are unavailable");
        goto finished;
    }

    switch_session(runner_session_id);
    saved_stdin = get_stdin();
    saved_stdout = get_stdout();
    saved_stderr = get_stderr();
    if (saved_stdin == NULL) saved_stdin = stdin;
    if (saved_stdout == NULL) saved_stdout = stdout;
    if (saved_stderr == NULL) saved_stderr = stderr;
    int command_fd = dup(fileno(task->writer));
    if (command_fd < 0 || (command_writer = fdopen(command_fd, "w")) == NULL) {
        if (command_fd >= 0) close(command_fd);
        write_runner_error(task, "could not create command output stream");
        goto restore_streams;
    }
    (void)setvbuf(command_writer, NULL, _IONBF, 0);
    set_streams(saved_stdin, command_writer, command_writer);

    pthread_mutex_lock(&tasks_mutex);
    int cancelled_before_start = task->cancelled;
    if (!cancelled_before_start) task->starting = 1;
    pthread_mutex_unlock(&tasks_mutex);
    if (cancelled_before_start) {
        status = 130;
        goto restore_streams;
    }

    pid_t pid = ios_fork();
    pthread_mutex_lock(&tasks_mutex);
    task->starting = 0;
    if (pid > 0) task->ios_pid = pid;
    int cancelled_after_fork = task->cancelled;
    pthread_cond_broadcast(&task->changed);
    pthread_mutex_unlock(&tasks_mutex);
    if (pid <= 0) {
        write_runner_error(task, "ios_fork failed to allocate a virtual process id");
        status = 127;
        goto restore_streams;
    }
    if (cancelled_after_fork) {
        release_pid(pid);
        status = 130;
        goto restore_streams;
    }

    command_stream_transferred = 1; /* ios_system's sh cleanup owns and closes this FILE*. */
    status = ios_system(task->command);
    ios_waitpid(pid);
    release_pid(pid);

restore_streams:
    if (command_writer != NULL && !command_stream_transferred) {
        fclose(command_writer);
        command_writer = NULL;
    }
    set_streams(saved_stdin, saved_stdout, saved_stderr);

finished:
    if (task->writer != NULL) {
        fflush(task->writer);
        fclose(task->writer);
        task->writer = NULL;
    }
    if (pool_pop != NULL && pool != NULL) {
        pool_pop(pool);
    }
    pthread_mutex_lock(&tasks_mutex);
    task->status = task->cancelled && status == 0 ? 130 : status;
    task->done = 1;
    pthread_cond_broadcast(&task->changed);
    maybe_clear_task_locked(task);
    pthread_mutex_unlock(&tasks_mutex);
    return NULL;
}

static PyObject *py_spawn(PyObject *self, PyObject *args)
{
    (void)self;
    const char *command;
    if (!PyArg_ParseTuple(args, "s:spawn", &command)) {
        return NULL;
    }
    const char *required_symbols[] = {
        "ios_switchSession", "ios_setStreams", "ios_stdin", "ios_stdout",
        "ios_stderr", "ios_fork", "ios_system", "ios_waitpid",
        "ios_releaseThreadId", "ios_getThreadId",
    };
    for (size_t index = 0; index < sizeof(required_symbols) / sizeof(required_symbols[0]); ++index) {
        if (ios_symbol(required_symbols[index]) == NULL) {
            errno = ENOSYS;
            return PyErr_Format(PyExc_OSError, "required ios_system symbol %s is unavailable",
                                required_symbols[index]);
        }
    }
    int fds[2];
    if (pipe(fds) != 0) {
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    (void)fcntl(fds[0], F_SETFD, FD_CLOEXEC);
    (void)fcntl(fds[1], F_SETFD, FD_CLOEXEC);
#ifdef F_SETNOSIGPIPE
    (void)fcntl(fds[1], F_SETNOSIGPIPE, 1);
#endif
    FILE *writer = fdopen(fds[1], "w");
    if (writer == NULL) {
        int saved_errno = errno;
        close(fds[0]);
        close(fds[1]);
        errno = saved_errno;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    (void)setvbuf(writer, NULL, _IONBF, 0);

    pthread_mutex_lock(&tasks_mutex);
    for (size_t index = 0; index < IOS_TASK_CAPACITY; ++index) {
        if (tasks[index].used && !tasks[index].done) {
            pthread_mutex_unlock(&tasks_mutex);
            fclose(writer);
            close(fds[0]);
            errno = EBUSY;
            return PyErr_SetFromErrno(PyExc_OSError);
        }
    }
    ios_task *task = NULL;
    for (size_t index = 0; index < IOS_TASK_CAPACITY; ++index) {
        if (!tasks[index].used) {
            task = &tasks[index];
            break;
        }
    }
    if (task == NULL) {
        pthread_mutex_unlock(&tasks_mutex);
        fclose(writer);
        close(fds[0]);
        errno = EBUSY;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    task->used = 1;
    task->done = 0;
    task->cancelled = 0;
    task->close_requested = 0;
    task->waiters = 0;
    task->starting = 0;
    task->ios_pid = 0;
    task->status = 0;
    task->id = next_task_id++;
    if (task->id == 0) {
        task->id = next_task_id++;
    }
    task->command = strdup(command);
    task->writer = writer;

    if (task->command == NULL) {
        clear_task_locked(task);
        pthread_mutex_unlock(&tasks_mutex);
        fclose(writer);
        close(fds[0]);
        return PyErr_NoMemory();
    }
    if (!task->condition_initialized) {
        int condition_error = pthread_cond_init(&task->changed, NULL);
        if (condition_error != 0) {
            clear_task_locked(task);
            pthread_mutex_unlock(&tasks_mutex);
            fclose(writer);
            close(fds[0]);
            errno = condition_error;
            return PyErr_SetFromErrno(PyExc_OSError);
        }
        task->condition_initialized = 1;
    }
    int thread_error = pthread_create(&task->worker, NULL, run_ios_task, task);
    if (thread_error != 0) {
        clear_task_locked(task);
        pthread_mutex_unlock(&tasks_mutex);
        fclose(writer);
        close(fds[0]);
        errno = thread_error;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    pthread_detach(task->worker);
    unsigned long long task_id = task->id;
    pthread_mutex_unlock(&tasks_mutex);
    PyObject *result = Py_BuildValue("Ki", task_id, fds[0]);
    if (result == NULL) {
        close(fds[0]);
        pthread_mutex_lock(&tasks_mutex);
        task->close_requested = 1;
        maybe_clear_task_locked(task);
        pthread_mutex_unlock(&tasks_mutex);
    }
    return result;
}

static PyObject *py_poll(PyObject *self, PyObject *args)
{
    (void)self;
    unsigned long long id;
    if (!PyArg_ParseTuple(args, "K:poll", &id)) {
        return NULL;
    }
    pthread_mutex_lock(&tasks_mutex);
    ios_task *task = find_task_locked(id);
    if (task == NULL) {
        pthread_mutex_unlock(&tasks_mutex);
        errno = ESRCH;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    if (!task->done) {
        pthread_mutex_unlock(&tasks_mutex);
        Py_RETURN_NONE;
    }
    int status = task->status;
    pthread_mutex_unlock(&tasks_mutex);
    return PyLong_FromLong(status);
}

static PyObject *py_wait(PyObject *self, PyObject *args)
{
    (void)self;
    unsigned long long id;
    PyObject *timeout_obj = Py_None;
    if (!PyArg_ParseTuple(args, "K|O:wait", &id, &timeout_obj)) {
        return NULL;
    }
    double timeout = -1.0;
    if (timeout_obj != Py_None) {
        timeout = PyFloat_AsDouble(timeout_obj);
        if (PyErr_Occurred()) {
            return NULL;
        }
        if (timeout < 0.0) {
            PyErr_SetString(PyExc_ValueError, "timeout must be non-negative or None");
            return NULL;
        }
    }

    struct timespec deadline;
    if (timeout >= 0.0) {
        if (clock_gettime(CLOCK_REALTIME, &deadline) != 0) {
            return PyErr_SetFromErrno(PyExc_OSError);
        }
        time_t seconds = (time_t)timeout;
        long nanoseconds = (long)((timeout - (double)seconds) * 1000000000.0);
        deadline.tv_sec += seconds;
        deadline.tv_nsec += nanoseconds;
        if (deadline.tv_nsec >= 1000000000L) {
            deadline.tv_sec++;
            deadline.tv_nsec -= 1000000000L;
        }
    }

    pthread_mutex_lock(&tasks_mutex);
    ios_task *task = find_task_locked(id);
    if (task == NULL) {
        pthread_mutex_unlock(&tasks_mutex);
        errno = ESRCH;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    task->waiters++;
    int wait_error = 0;
    Py_BEGIN_ALLOW_THREADS
    while (!task->done && wait_error == 0) {
        if (timeout < 0.0) {
            wait_error = pthread_cond_wait(&task->changed, &tasks_mutex);
        }
        else {
            wait_error = pthread_cond_timedwait(&task->changed, &tasks_mutex, &deadline);
        }
    }
    Py_END_ALLOW_THREADS
    PyObject *result = task->done ? PyLong_FromLong(task->status) : Py_NewRef(Py_None);
    task->waiters--;
    maybe_clear_task_locked(task);
    pthread_mutex_unlock(&tasks_mutex);
    if (wait_error != 0 && wait_error != ETIMEDOUT) {
        Py_DECREF(result);
        errno = wait_error;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    return result;
}

static PyObject *py_kill(PyObject *self, PyObject *args)
{
    (void)self;
    unsigned long long id;
    if (!PyArg_ParseTuple(args, "K:kill", &id)) {
        return NULL;
    }
    pthread_mutex_lock(&tasks_mutex);
    ios_task *task = find_task_locked(id);
    if (task == NULL) {
        pthread_mutex_unlock(&tasks_mutex);
        errno = ESRCH;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    task->cancelled = 1;
    pid_t pid = task->ios_pid;
    int done = task->done;
    pthread_mutex_unlock(&tasks_mutex);
    if (done) {
        Py_RETURN_NONE;
    }

    typedef pthread_t (*get_thread_fn)(pid_t);
    get_thread_fn get_thread = (get_thread_fn)ios_symbol("ios_getThreadId");
    if (get_thread == NULL) {
        errno = ENOSYS;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    struct timespec pause = {.tv_sec = 0, .tv_nsec = 10000000L};
    pthread_t command_thread = (pthread_t)0;
    int task_done = 0;
    Py_BEGIN_ALLOW_THREADS
    for (int elapsed = 0; elapsed < IOS_CANCEL_GRACE_MS; elapsed += 10) {
        pthread_mutex_lock(&tasks_mutex);
        task = find_task_locked(id);
        done = task == NULL || task->done;
        pid = task == NULL ? 0 : task->ios_pid;
        pthread_mutex_unlock(&tasks_mutex);
        if (done) {
            task_done = 1;
            break;
        }
        if (pid <= 0) {
            nanosleep(&pause, NULL);
            continue;
        }
        command_thread = get_thread(pid);
        if ((intptr_t)command_thread > 0) {
            break;
        }
        nanosleep(&pause, NULL);
    }
    Py_END_ALLOW_THREADS
    if (task_done) {
        Py_RETURN_NONE;
    }
    if ((intptr_t)command_thread <= 0) {
        errno = ETIMEDOUT;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    /* Match ios_system's pthread-cancel path; never signal the containing app process. */
    int cancel_error = pthread_cancel(command_thread);
    if (cancel_error != 0 && cancel_error != ESRCH) {
        errno = cancel_error;
        return PyErr_SetFromErrno(PyExc_OSError);
    }
    Py_RETURN_NONE;
}

static PyObject *py_close(PyObject *self, PyObject *args)
{
    (void)self;
    unsigned long long id;
    if (!PyArg_ParseTuple(args, "K:close", &id)) {
        return NULL;
    }
    pthread_mutex_lock(&tasks_mutex);
    ios_task *task = find_task_locked(id);
    if (task != NULL) {
        task->close_requested = 1;
        maybe_clear_task_locked(task);
    }
    pthread_mutex_unlock(&tasks_mutex);
    Py_RETURN_NONE;
}

static PyMethodDef ios_methods[] = {
    {"spawn", py_spawn, METH_VARARGS, "Start an ios_system command with pipe-captured output."},
    {"poll", py_poll, METH_VARARGS, "Return an ios_system command status, or None while running."},
    {"wait", py_wait, METH_VARARGS, "Wait for an ios_system command, optionally with a timeout."},
    {"kill", py_kill, METH_VARARGS, "Interrupt and cancel an ios_system command."},
    {"close", py_close, METH_VARARGS, "Release an ios_system task handle when it is done."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef ios_module = {
    PyModuleDef_HEAD_INIT,
    "_hermesios",
    "Native asynchronous ios_system process adapter for HermesLink.",
    -1,
    ios_methods,
};

PyMODINIT_FUNC PyInit__hermesios(void)
{
    return PyModule_Create(&ios_module);
}
