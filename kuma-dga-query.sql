SELECT 
    RequestUrl, domain(RequestUrl) AS domain,
    length(domain(RequestUrl)) AS domain_length,
    length(replaceRegexpAll(domain(RequestUrl), '[^0-9]', '')) * 1.0 / length(domain(RequestUrl)) AS digit_ratio,
    round(-arraySum(x -> (x / length(domain(RequestUrl))) * log2(x / length(domain(RequestUrl))), arrayMap(i -> 1, range(length(domain(RequestUrl))))), 3) AS entropy,
    round(entropy / log2(length(arrayDistinct(arrayMap(i -> substring(domain(RequestUrl), i+1, 1), range(length(domain(RequestUrl)))))) + 1), 3) AS norm_entropy,
    (length(replaceRegexpAll(domain(RequestUrl), '[^aeiou]', '')) + 1) * 1.0 / (length(replaceRegexpAll(domain(RequestUrl), '[^bcdfghjklmnpqrstvwxyz]', '')) + 1) AS vowel_consonant_ratio,
    length(splitByChar('.', domain(RequestUrl))) * 1.0 / length(domain(RequestUrl)) AS levels_ratio,
    length(arrayDistinct(arrayFilter(c -> match(c, '[a-z]’), arrayMap(i -> substring(domain(RequestUrl), i+1, 1), range(length(domain(RequestUrl))))))) AS unique_letters,
    IF(topLevelDomain(domain(RequestUrl)) IN ('tk','xyz','top','gq','ml','cf'), 1, 0) AS bad_tld_flag,
    IF(match(domain(RequestUrl), '^[0-9]'), 1, 0) AS starts_with_digit,
    arraySum(x -> length(x), extractAll(domain(RequestUrl), '[0-9]{2,}')) * 1.0 / length(domain(RequestUrl)) AS digit_seq_ratio,
    (length(arrayFilter(x -> x > 1, arrayMap(
                        l -> length(domain(RequestUrl)) - length(replaceRegexpAll(domain(RequestUrl), l, '')),
                        ['a','b','c','d','e','f','g','h','i','j','k','l','m','n','o','p','q','r','s','t','u','v','w','x','y','z']
                    )))) * 1.0 / (length(arrayFilter(x -> x > 0,
                    arrayMap(
                        l -> length(domain(RequestUrl)) - length(replaceRegexpAll(domain(RequestUrl), l, '')),
                        ['a','b','c','d','e','f','g','h','i','j','k','l','m','n','o','p','q','r','s','t','u','v','w','x','y','z']
                    ))) + 1) AS repeated_letter_ratio,
    arrayMax(arrayMap(x -> length(x), extractAll(domain(RequestUrl), '[bcdfghjklmnpqrstvwxyz]+'))) AS max_consonant_seq,
    countMatches(domain(RequestUrl), '-') AS hyphen_count,
    length(replaceRegexpAll(domain(RequestUrl), '[^-]', '')) * 1.0 / length(domain(RequestUrl)) AS hyphen_ratio,
    IF(match(domain(RequestUrl), '^[0-9]+'), 1, 0) AS starts_with_digits_flag,
    IF(match(domain(RequestUrl), '[0-9]+$'), 1, 0) AS ends_with_digits_flag,
    length(arrayDistinct(arrayFilter(c -> match(c, '[0-9]’), arrayMap(i -> substring(domain(RequestUrl), i+1, 1), range(length(domain(RequestUrl))))))) AS distinct_digits,
    IF(startsWith(domain(RequestUrl), 'xn--'), 1, 0) AS is_punycode,
    (0.010049736340256327 + domain_length * 1.2287055245865395 + digit_ratio * -2.0881107604042852 + entropy * -5.052030345090285 + norm_entropy * 1.3446060762089087 + vowel_consonant_ratio * 0.3691390458036087 + levels_ratio * -2.908721194583476 + unique_letters * 1.8131576517512755 + bad_tld_flag * 0.0 + starts_with_digit * -0.16176174578330935 + digit_seq_ratio * -0.11839387406321371 + repeated_letter_ratio * -1.3995632185846514 + max_consonant_seq * 2.854733044929207 + hyphen_count * -1.786436063827963 + hyphen_ratio * -1.3377014596156658 + starts_with_digits_flag * -0.16176174578330935 + ends_with_digits_flag * -0.43855760890533235 + distinct_digits * 4.14971363000855 + is_punycode * -0.12493404546276918) AS score,
    1.0 / (1.0 + exp(-score)) AS dga_regr,
    toString(floor(dga_regr)) AS is_dga
FROM `events`
WHERE NOT match(domain(RequestUrl), '^\\d+\\.\\d+\\.\\d+\\.\\d+$’) AND NOT match(domain(RequestUrl), '^[0-9a-fA-F:]+$’) AND RequestUrl != ''
ORDER BY dga_regr DESC
LIMIT 50;
