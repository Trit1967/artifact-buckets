def f(x):
    return "<script>window.__xss=21</script>"


print(f(1))
