#!/usr/bin/env python
#
#  TestRunner.py - This script is used to run arbitrary unit tests.  Unit
#  tests must contain the command used to run them in the input file, starting
#  immediately after a "RUN:" string.
#
#  This runner recognizes and replaces the following strings in the command:
#
#     %s - Replaced with the input name of the program, or the program to
#          execute, as appropriate.
#     %S - Replaced with the directory where the input resides.
#     %llvmgcc - llvm-gcc command
#     %llvmgxx - llvm-g++ command
#     %prcontext - prcontext.tcl script
#     %t - temporary file name (derived from testcase name)
#

import errno
import os
import platform
import re
import signal
import subprocess
import sys

# Increase determinism by explicitly choosing the environment.
kChildEnv = {}
for var in ('PATH', 'SYSTEMROOT'):
    kChildEnv[var] = os.environ.get(var, '')

kSystemName = platform.system()

class TestStatus:
    Pass = 0 
    XFail = 1
    Fail = 2
    XPass = 3
    Invalid = 4

    kNames = ['Pass','XFail','Fail','XPass','Invalid']
    @staticmethod
    def getName(code): 
        return TestStatus.kNames[code]

def mkdir_p(path):
    if not path:
        pass
    elif os.path.exists(path):
        pass
    else:
        parent = os.path.dirname(path) 
        if parent != path:
            mkdir_p(parent)
        try:
            os.mkdir(path)
        except OSError,e:
            if e.errno != errno.EEXIST:
                raise

def remove(path):
    try:
        os.remove(path)
    except OSError:
        pass

def cat(path, output):
    f = open(path)
    output.writelines(f)
    f.close()

def runOneTest(FILENAME, SUBST, OUTPUT, TESTNAME, CLANG, CLANGCC,
               useValgrind=False,
               useDGCompat=False,
               useScript=None, 
               output=sys.stdout):
    OUTPUT = os.path.abspath(OUTPUT)
    if useValgrind:
        VG_OUTPUT = '%s.vg'%(OUTPUT,)
        os.system('rm -f %s.*'%(VG_OUTPUT))
        VALGRIND = 'valgrind -q --tool=memcheck --leak-check=full --trace-children=yes --log-file=%s.%%p'%(VG_OUTPUT)
        CLANG    = '%s %s'%(VALGRIND, CLANG)
        CLANGCC  = '%s %s'%(VALGRIND, CLANGCC)

    # Create the output directory if it does not already exist.
    mkdir_p(os.path.dirname(OUTPUT))

    # FIXME
    #ulimit -t 40

    # FIXME: Load script once
    # FIXME: Support "short" script syntax

    if useScript:
        scriptFile = useScript
    else:
        # See if we have a per-dir test script.
        dirScriptFile = os.path.join(os.path.dirname(FILENAME), 'test.script')
        if os.path.exists(dirScriptFile):
            scriptFile = dirScriptFile
        else:
            scriptFile = FILENAME
            
    # Verify the script contains a run line.
    for ln in open(scriptFile):
        if 'RUN:' in ln:
            break
    else:
        print >>output, "******************** TEST '%s' HAS NO RUN LINE! ********************"%(TESTNAME,)
        output.flush()
        return TestStatus.Fail

    FILENAME = os.path.abspath(FILENAME)
    SCRIPT = OUTPUT + '.script'
    if kSystemName == 'Windows':
        SCRIPT += '.bat'
    TEMPOUTPUT = OUTPUT + '.tmp'

    substitutions = [('%s',SUBST),
                     ('%S',os.path.dirname(SUBST)),
                     ('%llvmgcc','llvm-gcc -emit-llvm -w'),
                     ('%llvmgxx','llvm-g++ -emit-llvm -w'),
                     ('%prcontext','prcontext.tcl'),
                     ('%t',TEMPOUTPUT),
                     (' clang ', ' ' + CLANG + ' '),
                     (' clang-cc ', ' ' + CLANGCC + ' ')]

    # Collect the test lines from the script.
    scriptLines = []
    xfailLines = []
    for ln in open(scriptFile):
        if 'RUN:' in ln:
            # Isolate the command to run.
            index = ln.index('RUN:')
            ln = ln[index+4:]
            
            # Strip trailing newline.
            scriptLines.append(ln)
        elif 'XFAIL' in ln:
            xfailLines.append(ln)
        
        # FIXME: Support something like END, in case we need to process large
        # files.
    
    # Apply substitutions to the script.
    def processLine(ln):
        # Apply substitutions
        for a,b in substitutions:
            ln = ln.replace(a,b)

        if useDGCompat:
            ln = re.sub(r'\{(.*)\}', r'"\1"', ln)

        # Strip the trailing newline and any extra whitespace.
        return ln.strip()
    scriptLines = map(processLine, scriptLines)    

    # Validate interior lines for '&&', a lovely historical artifact.
    for i in range(len(scriptLines) - 1):
        ln = scriptLines[i]

        if not ln.endswith('&&'):
            print >>output, "MISSING \'&&\': %s" % ln
            print >>output, "FOLLOWED BY   : %s" % scriptLines[i + 1]
            return TestStatus.Fail
    
        # Strip off '&&'
        scriptLines[i] = ln[:-2]

    if xfailLines:
        print >>output, "XFAILED '%s':"%(TESTNAME,)
        output.writelines(xfailLines)

    # Write script file
    f = open(SCRIPT,'w')
    if kSystemName == 'Windows':
        f.write('\nif %ERRORLEVEL% NEQ 0 EXIT\n'.join(scriptLines))
        f.write('\n')
    else:
        f.write(' &&\n'.join(scriptLines))
    f.close()

    outputFile = open(OUTPUT,'w')
    p = None
    try:
        if kSystemName == 'Windows':
            command = ['cmd','/c', SCRIPT]
        else:
            command = ['/bin/sh', SCRIPT]
        
        p = subprocess.Popen(command,
                             cwd=os.path.dirname(FILENAME),
                             stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE,
                             env=kChildEnv)
        out,err = p.communicate()
        outputFile.write(out)
        outputFile.write(err)
        SCRIPT_STATUS = p.wait()

        # Detect Ctrl-C in subprocess.
        if SCRIPT_STATUS == -signal.SIGINT:
            raise KeyboardInterrupt
    except KeyboardInterrupt:
        raise
    outputFile.close()

    if xfailLines:
        SCRIPT_STATUS = not SCRIPT_STATUS

    if useValgrind:
        if kSystemName == 'Windows':
            raise NotImplementedError,'Cannot run valgrind on windows'
        else:
            VG_OUTPUT = capture(['/bin/sh','-c','cat %s.*'%(VG_OUTPUT)])
        VG_STATUS = len(VG_OUTPUT)
    else:
        VG_STATUS = 0
    
    if SCRIPT_STATUS or VG_STATUS:
        print >>output, "******************** TEST '%s' FAILED! ********************"%(TESTNAME,)
        print >>output, "Command: "
        output.writelines(scriptLines)
        if not SCRIPT_STATUS:
            print >>output, "Output:"
        else:
            print >>output, "Incorrect Output:"
        cat(OUTPUT, output)
        if VG_STATUS:
            print >>output, "Valgrind Output:"
            print >>output, VG_OUTPUT
        print >>output, "******************** TEST '%s' FAILED! ********************"%(TESTNAME,)
        output.flush()
        if xfailLines:
            return TestStatus.XPass
        else:
            return TestStatus.Fail

    if xfailLines:
        return TestStatus.XFail
    else:
        return TestStatus.Pass

def capture(args):
    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out,_ = p.communicate()
    return out

def which(command):
    # Check for absolute match first.
    if os.path.exists(command):
        return command

    # Would be nice if Python had a lib function for this.
    paths = os.environ.get('PATH')
    if not paths:
        paths = os.defpath

    # Get suffixes to search.
    pathext = os.environ.get('PATHEXT', '').split(os.pathsep)

    # Search the paths...
    for path in paths.split(os.pathsep):
        for ext in pathext:
            p = os.path.join(path, command + ext)
            if os.path.exists(p):
                return p

    return None

def inferClang():
    # Determine which clang to use.
    clang = os.getenv('CLANG')
    
    # If the user set clang in the environment, definitely use that and don't
    # try to validate.
    if clang:
        return clang

    # Otherwise look in the path.
    clang = which('clang')

    if not clang:
        print >>sys.stderr, "error: couldn't find 'clang' program, try setting CLANG in your environment"
        sys.exit(1)
        
    return clang

def inferClangCC(clang):
    clangcc = os.getenv('CLANGCC')

    # If the user set clang in the environment, definitely use that and don't
    # try to validate.
    if clangcc:
        return clangcc

    # Otherwise try adding -cc since we expect to be looking in a build
    # directory.
    if clang.endswith('.exe'):
        clangccName = clang[:-4] + '-cc.exe'
    else:
        clangccName = clang + '-cc'
    clangcc = which(clangccName)
    if not clangcc:
        # Otherwise ask clang.
        res = capture([clang, '-print-prog-name=clang-cc'])
        res = res.strip()
        if res and os.path.exists(res):
            clangcc = res
    
    if not clangcc:
        print >>sys.stderr, "error: couldn't find 'clang-cc' program, try setting CLANGCC in your environment"
        sys.exit(1)
        
    return clangcc
    
def getTestOutputBase(dir, testpath):
    """getTestOutputBase(dir, testpath) - Get the full path for temporary files
    corresponding to the given test path."""

    # Form the output base out of the test parent directory name and the test
    # name. FIXME: Find a better way to organize test results.
    return os.path.join(dir, 
                        os.path.basename(os.path.dirname(testpath)),
                        os.path.basename(testpath))
                      
def main():
    global options
    from optparse import OptionParser
    parser = OptionParser("usage: %prog [options] {tests}")
    parser.add_option("", "--clang", dest="clang",
                      help="Program to use as \"clang\"",
                      action="store", default=None)
    parser.add_option("", "--clang-cc", dest="clangcc",
                      help="Program to use as \"clang-cc\"",
                      action="store", default=None)
    parser.add_option("", "--vg", dest="useValgrind",
                      help="Run tests under valgrind",
                      action="store_true", default=False)
    parser.add_option("", "--dg", dest="useDGCompat",
                      help="Use llvm dejagnu compatibility mode",
                      action="store_true", default=False)
    (opts, args) = parser.parse_args()

    if not args:
        parser.error('No tests specified')

    if opts.clang is None:
        opts.clang = inferClang()
    if opts.clangcc is None:
        opts.clangcc = inferClangCC(opts.clang)

    for path in args:
        command = path
        output = getTestOutputBase('Output', path) + '.out'
        testname = path
        
        res = runOneTest(path, command, output, testname, 
                         opts.clang, opts.clangcc,
                         useValgrind=opts.useValgrind,
                         useDGCompat=opts.useDGCompat,
                         useScript=os.getenv("TEST_SCRIPT"))

    sys.exit(res == TestStatus.Fail or res == TestStatus.XPass)

if __name__=='__main__':
    main()
