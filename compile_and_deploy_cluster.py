#! /usr/bin/env python3
# -*- coding: utf-8 -*-

from typing import *
import os
import argparse
import subprocess
import re
import socket

work_dir = os.path.abspath(os.curdir)
hostname = socket.gethostname()
ip = socket.gethostbyname(hostname)
username = os.getlogin()

parser = argparse.ArgumentParser(description='compile and deploy cluster from source code directory')
parser.add_argument('--src_dir', type=str, default=work_dir,
                    help='OB源代码绝对路径。若为空则默认为当前执行路径')
parser.add_argument('--deploy_ip_list', type=str, nargs='+', default=[ip, ip],
                    help='要部署的IP列表，如果在同一IP上部署多个observer需要把IP重写多次。若为空则默认本机两副本部署')
parser.add_argument('--deploy_dir', type=str, default='/data/1/'+username,
                    help='部署目录名称，绝对路径，若目录不存在则新建。若为空则默认为/data/1/$USER')
parser.add_argument('--compile', default=None, choices=['debug', 'release'], type=str,
                    help='本次部署是否需要从源代码编译出一个新的binary，若需要重编译请填写编译模式')
parser.add_argument('--tag', type=str, default='test',
                    help='生成的OBD镜像名称以及部署的集群名称。若为空则默认为test')
parser.add_argument('--with_admin',  default=False, action ='store_true',
                    help='是否编译ob_admin'),
parser.add_argument('--enable_oracle', default=False, action='store_true',
                    help='是否生成Oracle租户'),
parser.add_argument('--memory_limit', type=str, default='8G',
                   help='单个observer的内存限制。若为空则默认为8G'),
parser.add_argument('--cpu_count', type=str, default='4',
                   help='单个observer的CPU限制。若为空则默认为4'),
parser.add_argument('--devname', type=str, default=None,
                    help='部署服务器的网卡名称。若为空则将自动获取')


args = parser.parse_args()

server_config = ''
for idx in range(len(args.deploy_ip_list)):
  server_config += '\n    - name: server{}\n      ip: {}'.format(idx + 1, args.deploy_ip_list[idx])
ip_used_port = {}
server_config_detail = ''
for idx in range(len(args.deploy_ip_list)):
  if args.deploy_ip_list[idx] not in ip_used_port:
    ip_used_port[args.deploy_ip_list[idx]] = 3881
  server_config_detail += '\n  server{}:\n    mysql_port: {}\n    rpc_port: {}\n    home_path: {}/z{}\n    zone: zone{}'.format(
    idx + 1,
    ip_used_port[args.deploy_ip_list[idx]],
    ip_used_port[args.deploy_ip_list[idx]] + 1,
    args.deploy_dir,
    idx + 1,
    idx + 1
  )
  ip_used_port[args.deploy_ip_list[idx]] += 2

# 获取网卡名
if args.devname:
  devname = args.devname
else:
  cmd = '''awk '{a[NR]=$0}END{for(i = 0; i < length(a); i++){ if (index(a[i], \"'''+args.deploy_ip_list[0] +'''\")!=0) {print a[i-1]}}}' | awk '{match($0, /([a-zA-Z0-9]+)/, a); print a[1]}' '''
  cmd = '''ssh {} "bash -c /usr/sbin/ifconfig" | {}'''.format(args.deploy_ip_list[0], cmd)
  devname = os.popen(cmd).read().strip()
print('devname:{}'.format(devname))
# 生成定制的yaml文件
config_yaml = '''oceanbase-ce:
  tag: {}
  servers:{}
  global:
    devname: {}
    cluster_id: 1
    memory_limit: {} # The maximum running memory for an observer
    system_memory: 3G # The reserved system memory. system_memory is reserved for general tenants.
    cpu_count: {} # The assigned cpu for an observer
    datafile_size: 10G # The disk space for data storage.
    log_disk_size: 10G # The disk space for redo log storage.
    syslog_level: TRACE # System log level. The default value is INFO.
    syslog_io_bandwidth_limit: 2G
    enable_syslog_wf: false # Print system logs whose levels are higher than WARNING to a separate log file. The default value is true.
    enable_syslog_recycle: true # Enable auto system log recycling or not. The default value is false.
    max_syslog_file_count: 100 # The maximum number of reserved log files before enabling auto recycling. The default value is 0.
    __min_full_resource_pool_memory: 1073741824 # Lower bound of memory for resource pool, default 5G{}'''.format(args.tag, server_config, devname, args.memory_limit, args.cpu_count, server_config_detail)
with open('config.yaml', 'w') as f:
  f.write(config_yaml)

def assert_notice(cmd : str, info : str = None):
  print('[CMD]{}'.format(cmd))
  assert(0 == os.system(cmd))
  if str:
    print('[NOTICE]{}'.format(info))

work_dir = os.path.abspath(os.curdir)
os.chdir(args.src_dir)
assert_notice('obd devmode enable')
if args.compile:# 编译源代码
  assert_notice('./build.sh clean')
  assert_notice('./build.sh clean')
  assert_notice('./build.sh {} --init'.format(args.compile))
  assert_notice('ob-make -C ./build_{}/src/observer/'.format(args.compile), 'compile source code done')
  if args.with_admin:
    assert_notice('ob-make -C ./build_{}/tools/ob_admin/'.format(args.compile), 'compile ob_admin done')
os.chdir('./tools/deploy')
assert_notice('./copy.sh', 'copy needed file done')
if args.compile:# 拷贝编译出来的二进制文件
  assert_notice('cp ../../build_{}/src/observer/observer ./bin/'.format(args.compile), 'copy new binary done')
output = subprocess.Popen('./bin/observer -V', shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
stdout, stderr = output.communicate()
total = stdout.decode('ascii') + stderr.decode('ascii')
version = re.search(r'observer \(.*?(\d.*?)\)', total, re.I).group(1)
assert_notice('rm -rf ~/.obd/repository/oceanbase-ce/*', 'delete old compiled binary in obd repo')
assert_notice('obd mirror create -n oceanbase-ce -p {}/tools/deploy/ -V {} -t {} -f'.format(args.src_dir, version, args.tag), 'generate mirror done, tag:{}'.format(args.tag))# 打包OBD镜像
os.chdir(work_dir)
if int(os.popen('obd cluster list | grep \'{}.*running\' | wc -l'.format(args.tag)).read().strip()) != 0:# 如果同名集群已经启动，需要先销毁正在运行的集群
  assert_notice('obd cluster stop {}'.format(args.tag), 'stop running cluster done, tag:{}'.format(args.tag))
  assert_notice('obd cluster destroy {}'.format(args.tag), 'destroy stopped running cluster done, tag:{}'.format(args.tag))
if int(os.popen('obd cluster list | grep \'{}\' | grep -v \'destroyed\' | wc -l'.format(args.tag)).read().strip()) != 0:# 如果同名集群已经启动，需要先销毁正在运行的集群
  assert_notice('obd cluster destroy {}'.format(args.tag), 'destroy stopped running cluster done, tag:{}'.format(args.tag))
assert_notice('obd cluster deploy {} -c config.yaml'.format(args.tag), 'cluster deploy done, cluster:{}'.format(args.tag))
if args.compile and args.with_admin:
  for ip in args.deploy_ip_list:
    assert_notice('scp {}/build_{}/tools/ob_admin/ob_admin {}:{}'.format(args.src_dir, args.compile, ip, args.deploy_dir), 'copy ob_admin to deloy dir done')
assert_notice('obd cluster start {}'.format(args.tag), 'cluster start done, cluster:{}'.format(args.tag))
#assert_notice('obd cluster tenant create {} -n mysql --max-cpu 2 --mode mysql'.format(args.tag), 'cluster create mysql tenant done, cluster:{}, tenant:mysql'.format(args.tag))
if args.enable_oracle:
	assert_notice('obd cluster tenant create {} -n oracle --max-cpu 2 --mode oracle'.format(args.tag), 'cluster create oracle tenant done, cluster:{}, tenant:oracle'.format(args.tag))
