#!/bin/bash

# The expected tests format is backend:system:task[:variant]
order_tests() {
    tests="$@"
    priority=500
    for test in $tests; do
        task="$(echo "$test" | tr : ' ' | awk '{print $3}')/task.yaml"
        cp "$task" "$task".back
        sed -i '/priority:/d' "$task"   
        echo "priority: $priority" >> "$task"
        priority=$(($priority - 1))
    done
}

# The expected tests format is backend:system:task[:variant]
restore_tests() {
    tests="$@"
    for test in $tests; do
        task="$(echo "$test" | tr : ' ' | awk '{print $3}')/task.yaml"
        mv "$task".back "$task"
    done
}
