```bash
cloc  --exclude-dir=react-native-app,load-generator --not-match-d='internal/tools' --strip-comments=nc --vcs
=git
```

```
------------------------------------------------------------------------------------
Language                          files          blank        comment           code
------------------------------------------------------------------------------------
C++                                   5           1371            890          10306
TypeScript                          103           1037            249           6714
Go                                    8            712            372           4452
JavaScript                           12            229            243           1286
Elixir                               31            296            497           1045
Python                                6            233            178           1035
C#                                   10            150             41            696
Java                                  4             82            106            401
Rust                                  6             69             13            313
PHP                                   6             59             32            193
Kotlin                                1             10             11             67
Ruby                                  1             16             14             53
```