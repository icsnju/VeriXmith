# This is designed to be run inside the Docker container.

number=${1:?"the number of files to be generated"}
config_file=${2:?"the configuration file for Verismith"}
output_directory=${3:?"the destination to save output files"}
max_loc=${4:?"max lines of generated program"}

for ((i = 0; i < $number; i = i + 1)); do
    filepath="$output_directory/vtest$i.v"
    until [ -f $filepath ] && [ $(wc -l <$filepath) -le $max_loc ]; do
        result/bin/verismith generate -c $config_file -o $filepath
    done
done
