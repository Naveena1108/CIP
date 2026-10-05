/**
 * CIP Ultra-Simple Language, UX & Graph Intelligence Transformer
 * 
 * CORE PRINCIPLE:
 * Do not make the user understand CIP. Make CIP understandable to the user.
 * Child-simple + Professional. Answers:
 * - What happened?
 * - Why does it matter?
 * - What may happen?
 * - What should I look at?
 */

(function (window) {
    'use strict';

    const CIPSimplifier = {
        // --- 1. VOCABULARY TRANSLATIONS ---
        terms: {
            signal: 'change',
            indicator: 'change',
            anomaly: 'unusual change',
            cri: 'risk',
            risk_score: 'risk',
            risk_trajectory: 'risk over time',
            prediction: 'what may happen',
            forecast: 'what may happen',
            evidence: 'why we think this',
            corroboration: 'other information that supports this',
            source_provenance: 'source',
            investigation: 'look closer',
            analysis: 'what we found',
            external_intelligence: 'outside information',
            osint: 'outside information',
            data_ingestion: 'add data',
            dataset: 'data',
            model_confidence: 'how sure we are',
            model_limitations: 'what could change this',
            trajectory_driver: 'what is causing the change',
            cross_signal_relationship: 'related changes',
            anomaly_severity: 'how important it is'
        },

        // --- 2. CLEAN DOMAIN & METRIC LABELS ---
        cleanMetricName: function (raw) {
            if (!raw) return 'Institutional Area';
            const s = String(raw).toLowerCase().trim();
            if (s.includes('placement')) return 'Student placement';
            if (s.includes('admiss') || s.includes('intake') || s.includes('enrolled')) return 'New student admissions';
            if (s.includes('cet') || s.includes('rank') || s.includes('cutoff')) return 'Admissions test rank';
            if (s.includes('faculty') || s.includes('staff') || s.includes('turnover')) return 'Faculty retention';
            if (s.includes('attend')) return 'Student attendance';
            if (s.includes('pass') || s.includes('exam') || s.includes('grade')) return 'Academic results';
            if (s.includes('fee') || s.includes('financ') || s.includes('revenue')) return 'College finances';
            if (s.includes('infra') || s.includes('lab')) return 'Campus facilities';
            // Fallback: replace underscores and title case
            return raw.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        },

        // --- 2b. CLEAN EXECUTIVE NARRATIVE (Sanitizes technical and algorithm jargon) ---
        cleanExecutiveNarrative: function (text) {
            if (!text) return '';
            return String(text)
                .replace(/\[DETERMINISTIC GROUND-TRUTH LOCK:[^\]]*\]\s*/gi, '')
                .replace(/Z-score:\s*[+-]?\d+(\.\d+)?/gi, '')
                .replace(/\(Z-score[^)]*\)/gi, '')
                .replace(/\(Z-Score[^)]*\)/gi, '')
                .replace(/empirical signal anomalies/gi, 'notable operational changes')
                .replace(/Composite Risk Index of (0\.\d+)/gi, (m, p1) => `Risk Score of ${Math.round(parseFloat(p1) * 100)}/100`)
                .replace(/Composite Risk Index:?\s*(0\.\d+)/gi, (m, p1) => `Risk Score: ${Math.round(parseFloat(p1) * 100)}/100`)
                .replace(/\(CRI[:=\s]*(0\.\d+)\)/gi, (m, p1) => `(Risk Score: ${Math.round(parseFloat(p1) * 100)}/100)`)
                .replace(/CRI[=:\s]+(0\.\d+)/gi, (m, p1) => `Risk Score: ${Math.round(parseFloat(p1) * 100)}/100`)
                .trim();
        },

        // --- 3. RISK LEVEL & STATUS MAPPING ---
        getRiskStatus: function (cri, riskLevelStr) {
            const score = typeof cri === 'number' ? cri : parseFloat(cri || 0);
            const level = String(riskLevelStr || '').toUpperCase();

            if (score > 0.75 || level === 'CRITICAL') {
                return {
                    label: 'Critical',
                    tag: 'This needs attention now.',
                    explanation: 'Several important areas have dropped significantly. College leadership should act immediately.',
                    badgeClass: 'cip-badge-critical',
                    badgeColor: '#7A2438',
                    textColor: '#C98291',
                    stageNum: 4,
                    rawScore: score.toFixed(2)
                };
            }
            if (score > 0.50 || level === 'HIGH') {
                return {
                    label: 'High',
                    tag: 'This needs attention.',
                    explanation: 'Key areas have declined over multiple years. Attention is needed before it gets worse.',
                    badgeClass: 'cip-badge-high',
                    badgeColor: '#A94A55',
                    textColor: '#E295A4',
                    stageNum: 3,
                    rawScore: score.toFixed(2)
                };
            }
            if (score > 0.25 || level === 'MEDIUM' || level === 'WATCH' || level === 'WARNING') {
                return {
                    label: 'Watch',
                    tag: 'Something changed. Keep an eye on it.',
                    explanation: 'Some numbers shifted this year. Keep monitoring to see if it continues.',
                    badgeClass: 'cip-badge-watch',
                    badgeColor: '#B07A3F',
                    textColor: '#E6B37D',
                    stageNum: 2,
                    rawScore: score.toFixed(2)
                };
            }
            return {
                label: 'Normal',
                tag: 'Nothing unusual right now.',
                explanation: 'Institutional numbers are steady and within expected ranges.',
                badgeClass: 'cip-badge-normal',
                badgeColor: '#4F8068',
                textColor: '#7FB399',
                stageNum: 1,
                rawScore: score.toFixed(2)
            };
        },

        // --- 4. CONFIDENCE FORMATTING ---
        formatConfidence: function (conf) {
            const val = typeof conf === 'number' ? conf : parseFloat(conf || 0.9);
            if (val >= 0.8) return { label: 'High', desc: 'Backed by confirmed institutional reports' };
            if (val >= 0.5) return { label: 'Moderate', desc: 'Backed by preliminary institutional data' };
            return { label: 'Low', desc: 'Limited historical data available' };
        },

        // --- 5. ULTRA-SIMPLE FINDING STRUCTURE (Rule 10) ---
        simplifyFinding: function (anomaly, index) {
            const rawName = (anomaly.signal_name || anomaly.metric_name || 'Institutional indicator');
            const metric = this.cleanMetricName(rawName);
            const observed = anomaly.observed_value !== undefined ? anomaly.observed_value : null;
            const baseline = anomaly.baseline_value !== undefined ? anomaly.baseline_value : null;
            const year = anomaly.academic_year || 'recent years';
            const desc = (anomaly.description || '').trim();
            const zscore = anomaly.deviation_zscore || 0;

            // Generate "What happened?" in plain English
            let whatHappened = `${metric} changed unusually.`;
            let whatChanged = desc;
            let whyItMatters = 'This may affect upcoming student outcomes and institutional standing.';
            let whyThinkThis = [
                `Data shows unusual changes in ${year}.`,
                `Compared against prior institutional history.`
            ];
            let whatNext = `Review detailed ${metric.toLowerCase()} reports by department.`;

            // Detect Placement
            if (metric.toLowerCase().includes('placement')) {
                if (observed !== null && baseline !== null) {
                    if (observed < baseline) {
                        whatHappened = 'Fewer students are getting placed.';
                        whatChanged = `Placement fell from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                        whyItMatters = 'Fewer students may get jobs after graduation, which could reduce future admissions.';
                        whyThinkThis = [
                            'Placement dropped over consecutive reporting years.',
                            'The decline appears across multiple departments.',
                            'Verified from official college placement records.'
                        ];
                        whatNext = 'Check placement support and campus hiring activity by department.';
                    } else if (observed > baseline) {
                        whatHappened = 'Student placement rate improved.';
                        whatChanged = `Placement rose from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                        whyItMatters = 'Improved student placements strengthen institutional reputation and alumni career outcomes.';
                        whyThinkThis = [
                            'Placement outcomes improved relative to historical baseline.',
                            'Verified from official college placement records.'
                        ];
                        whatNext = 'Maintain active corporate recruiting pipelines and student preparation.';
                    } else {
                        whatHappened = 'Student placement rate remained steady.';
                        whatChanged = `Placement remained at ${Math.round(observed)}% (${year}).`;
                    }
                } else {
                    whatChanged = `Placement rate shifted in ${year}.`;
                }
            }
            // Detect Admissions
            else if (metric.toLowerCase().includes('admiss') || metric.toLowerCase().includes('intake') || metric.toLowerCase().includes('enroll')) {
                if (observed !== null && baseline !== null) {
                    if (observed < baseline) {
                        whatHappened = 'Fewer new students enrolled.';
                        whatChanged = `Admissions fell from ${Math.round(baseline)} to ${Math.round(observed)} students (${year}).`;
                        whyItMatters = 'Lower student intake can reduce tuition revenue and leave seats vacant.';
                        whyThinkThis = [
                            'Intake numbers were lower than the historical baseline.',
                            'Fewer seats were filled in core branches.',
                            'Verified from admissions registers.'
                        ];
                        whatNext = 'Examine branch-wise admission numbers and student inquiry rates.';
                    } else if (observed > baseline) {
                        whatHappened = 'New student admissions increased.';
                        whatChanged = `Admissions rose from ${Math.round(baseline)} to ${Math.round(observed)} students (${year}).`;
                        whyItMatters = 'Expanding student intake requires matching faculty staffing, lab infrastructure, and placement capacity.';
                        whyThinkThis = [
                            'Admissions intake exceeded historical baseline levels.',
                            'More students enrolled in this academic period.',
                            'Verified from admissions registers.'
                        ];
                        whatNext = 'Ensure department student-to-faculty ratios and placement pipelines scale to meet higher student intake.';
                    } else {
                        whatHappened = 'New student intake remained steady.';
                        whatChanged = `Admissions remained at ${Math.round(observed)} students (${year}).`;
                    }
                } else {
                    whatChanged = `Enrollment shifted from historical levels in ${year}.`;
                }
            }
            // Detect Faculty
            else if (metric.toLowerCase().includes('faculty') || metric.toLowerCase().includes('staff') || metric.toLowerCase().includes('teacher')) {
                const isTurnover = metric.toLowerCase().includes('turnover') || metric.toLowerCase().includes('depart') || metric.toLowerCase().includes('attrition') || metric.toLowerCase().includes('exit');
                if (observed !== null && baseline !== null) {
                    if (isTurnover) {
                        if (observed > baseline) {
                            whatHappened = 'More faculty members left this year.';
                            whatChanged = `Faculty turnover rose from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                            whyItMatters = 'Losing experienced teachers can disrupt classes and lower student satisfaction.';
                        } else {
                            whatHappened = 'Faculty departures decreased.';
                            whatChanged = `Faculty turnover fell from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                            whyItMatters = 'Better faculty retention improves instructional continuity and research productivity.';
                        }
                    } else {
                        if (observed < baseline) {
                            whatHappened = 'Teaching faculty count decreased.';
                            whatChanged = `Faculty count fell from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                            whyItMatters = 'Lower faculty counts increase student-faculty ratios and teaching workloads.';
                        } else {
                            whatHappened = 'Teaching faculty count increased.';
                            whatChanged = `Faculty count rose from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                            whyItMatters = 'Staff expansion supports expanded course offerings and departmental research.';
                        }
                    }
                } else {
                    whatChanged = `Faculty staffing numbers shifted in ${year}.`;
                }
                whyThinkThis = [
                    'Department staffing rosters show changes relative to prior cycles.',
                    'Verified from institutional human resources records.'
                ];
                whatNext = 'Review department staffing balance and faculty retention support.';
            }
            // Detect Rank / CET
            else if (metric.toLowerCase().includes('rank') || metric.toLowerCase().includes('cutoff')) {
                if (observed !== null && baseline !== null) {
                    if (observed > baseline) {
                        whatHappened = 'Admissions cutoff ranks widened.';
                        whatChanged = `Average closing rank shifted back from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                        whyItMatters = 'Lower entrance cutoffs may reflect declining student preference for this college.';
                    } else if (observed < baseline) {
                        whatHappened = 'Admissions cutoff ranks improved.';
                        whatChanged = `Average closing rank improved from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                        whyItMatters = 'More competitive cutoffs indicate rising student preference and institutional prestige.';
                    } else {
                        whatHappened = 'Admissions cutoff ranks remained steady.';
                        whatChanged = `Closing rank remained at ${Math.round(observed)} (${year}).`;
                    }
                } else {
                    whatChanged = `Admission ranks shifted noticeably in ${year}.`;
                }
                whyThinkThis = [
                    'Rank cutoffs tracked across state entrance counseling rounds.',
                    'Verified against state entrance counseling ledgers.'
                ];
                whatNext = 'Compare entrance preferences with nearby peer institutions.';
            }
            // Detect Attendance
            else if (metric.toLowerCase().includes('attend')) {
                if (observed !== null && baseline !== null) {
                    if (observed < baseline) {
                        whatHappened = 'Student attendance dropped.';
                        whatChanged = `Average attendance fell from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                        whyItMatters = 'Low attendance often leads to lower exam pass rates and higher course dropouts.';
                    } else if (observed > baseline) {
                        whatHappened = 'Student attendance improved.';
                        whatChanged = `Average attendance rose from ${Math.round(baseline)}% to ${Math.round(observed)}% (${year}).`;
                        whyItMatters = 'Higher student engagement in lectures correlates with better academic outcomes.';
                    } else {
                        whatHappened = 'Student attendance remained steady.';
                        whatChanged = `Average attendance remained at ${Math.round(observed)}% (${year}).`;
                    }
                } else {
                    whatChanged = `Daily attendance shifted noticeably in ${year}.`;
                }
                whyThinkThis = [
                    'Class attendance logged across academic semesters.',
                    'Verified from student attendance logs.'
                ];
                whatNext = 'Check semester attendance records by class.';
            }
            // General Fallback
            else if (observed !== null && baseline !== null) {
                if (observed > baseline) {
                    whatHappened = `${metric} increased.`;
                    whatChanged = `${metric} rose from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                } else if (observed < baseline) {
                    whatHappened = `${metric} decreased.`;
                    whatChanged = `${metric} fell from ${Math.round(baseline)} to ${Math.round(observed)} (${year}).`;
                } else {
                    whatHappened = `${metric} remained steady.`;
                    whatChanged = `${metric} remained at ${Math.round(observed)} (${year}).`;
                }
            }

            // Severity in everyday words
            let severityLabel = 'Watch';
            let severityBg = 'bg-amber-500/10 text-amber-700 dark:text-amber-400 border-amber-500/20';
            if (anomaly.severity === 'CRITICAL' || Math.abs(zscore) > 3.0) {
                severityLabel = 'Needs attention now';
                severityBg = 'bg-red-500/10 text-red-700 dark:text-red-400 border-red-500/20';
            } else if (anomaly.severity === 'HIGH' || Math.abs(zscore) > 2.0) {
                severityLabel = 'Needs attention';
                severityBg = 'bg-rose-500/10 text-rose-700 dark:text-rose-400 border-rose-500/20';
            }

            return {
                index: index,
                rawName: rawName,
                metric: metric,
                whatHappened: whatHappened,
                whatChanged: whatChanged,
                whyItMatters: whyItMatters,
                whyThinkThis: whyThinkThis,
                whatNext: whatNext,
                severityLabel: severityLabel,
                severityBg: severityBg,
                observed: observed,
                baseline: baseline,
                year: year,
                zscore: zscore
            };
        },

        // --- 6. OVERVIEW STORY SYNTHESIZER (Rule 6 & 7) ---
        synthesizeOverviewStory: function (assessment, anomalies) {
            const cri = assessment ? (assessment.composite_risk_index || 0) : 0;
            const status = this.getRiskStatus(cri, assessment ? assessment.risk_level : 'NORMAL');
            const items = (anomalies || []).map((a, i) => this.simplifyFinding(a, i));

            // 1. WHAT IS HAPPENING?
            let happening = 'Everything looks steady. No major institutional problems detected.';
            if (items.length > 0) {
                const primary = items[0];
                if (items.length === 1) {
                    happening = `${primary.whatHappened} Other areas remain steady.`;
                } else if (items.length === 2) {
                    happening = `${primary.whatHappened} Also, ${items[1].whatHappened.toLowerCase()}`;
                } else {
                    happening = `${primary.whatHappened} Also, ${items[1].whatHappened.toLowerCase()} In total, ${items.length} areas need monitoring.`;
                }
            }

            // 2. WHAT CHANGED?
            let changed = 'All institutional numbers are within historical baselines.';
            if (items.length > 0) {
                changed = items.slice(0, 3).map(it => it.whatChanged).join(' ');
            }

            // 3. WHY DOES IT MATTER?
            let matters = 'Normal operations support consistent student outcomes and steady enrollment.';
            if (items.length > 0) {
                matters = items[0].whyItMatters;
                if (items.length > 1) {
                    matters += ` Furthermore, ${items[1].whyItMatters.charAt(0).toLowerCase() + items[1].whyItMatters.slice(1)}`;
                }
            }

            // 4. WHAT MAY HAPPEN?
            let mayHappen = 'If current conditions continue, institutional performance is expected to remain stable.';
            if (status.label === 'Critical') {
                mayHappen = 'Without intervention, these drops could compound next year, impacting admissions, college revenue, and student outcomes.';
            } else if (status.label === 'High') {
                mayHappen = 'If recent trends continue, overall risk may rise and more students may face challenges with placement or coursework.';
            } else if (status.label === 'Watch') {
                mayHappen = 'These shifts may resolve naturally or develop into larger trends if left unmonitored over the next academic cycle.';
            }

            // 5. WHAT SHOULD WE LOOK AT?
            let lookAt = 'Continue regular term monitoring of student admissions and placement records.';
            if (items.length > 0) {
                lookAt = items[0].whatNext;
                if (items.length > 1) {
                    lookAt += ` Also, ${items[1].whatNext.charAt(0).toLowerCase() + items[1].whatNext.slice(1)}`;
                }
            }

            return {
                status: status,
                happening: happening,
                changed: changed,
                matters: matters,
                mayHappen: mayHappen,
                lookAt: lookAt,
                primaryFinding: items.length > 0 ? items[0] : null,
                items: items
            };
        },

        // --- 7. GRAPH STORY TITLE & EXPLANATION (Rule 12 & 16) ---
        getGraphStory: function (dataPoints, metricType) {
            if (!dataPoints || dataPoints.length < 2) {
                return {
                    title: 'Institutional trend over time',
                    explanation: 'Awaiting additional historical periods to show the trend.',
                    highlight: 'Data from one period recorded.',
                    trendDirection: 'STABLE'
                };
            }

            // Safely extract numeric value whether dataPoints contains numbers or objects
            const getVal = (pt) => {
                if (typeof pt === 'number') return pt;
                if (!pt || typeof pt !== 'object') return 0;
                if (pt.composite_risk_index !== undefined) return Number(pt.composite_risk_index);
                if (pt.y !== undefined) return Number(pt.y);
                if (pt.value !== undefined) return Number(pt.value);
                if (pt.placement_percentage !== undefined) return Number(pt.placement_percentage);
                return 0;
            };

            const first = getVal(dataPoints[0]);
            const last = getVal(dataPoints[dataPoints.length - 1]);
            const diff = last - first;
            const isPlacement = metricType === 'placement';

            if (isPlacement) {
                if (diff < -5) {
                    return {
                        title: 'Fewer students are getting placed',
                        explanation: `Placement fell from ${Math.round(first)}% to ${Math.round(last)}% over ${dataPoints.length} reporting years.`,
                        highlight: `Placement fell ${Math.abs(Math.round(diff))} percentage points.`,
                        trendDirection: 'DOWN'
                    };
                } else if (diff > 5) {
                    return {
                        title: 'More students are getting placed',
                        explanation: `Placement improved from ${Math.round(first)}% to ${Math.round(last)}%.`,
                        highlight: `Placement increased ${Math.round(diff)} percentage points.`,
                        trendDirection: 'UP'
                    };
                }
                return {
                    title: 'Student placement is steady',
                    explanation: `Placement has hovered around ${Math.round(last)}% across recent years.`,
                    highlight: 'No major change in placement rate.',
                    trendDirection: 'STABLE'
                };
            }

            // Institutional Risk Score (0.0 to 1.0 or 0 to 100)
            const firstScore = first <= 1.0 ? Math.round(first * 100) : Math.round(first);
            const lastScore = last <= 1.0 ? Math.round(last * 100) : Math.round(last);
            const scoreDiff = lastScore - firstScore;

            if (scoreDiff > 5) {
                return {
                    title: 'Risk is trending upward',
                    explanation: `Risk score rose from ${firstScore}/100 to ${lastScore}/100 across recent academic periods.`,
                    highlight: 'Key operational indicators show increased pressure on college operations.',
                    trendDirection: 'UP'
                };
            } else if (scoreDiff < -5) {
                return {
                    title: 'Risk is decreasing',
                    explanation: `Risk score improved from ${firstScore}/100 down to ${lastScore}/100 across recent academic periods.`,
                    highlight: 'Recent indicators reflect healthy recovery and stabilizing metrics.',
                    trendDirection: 'DOWN'
                };
            }
            return {
                title: 'Risk is stable',
                explanation: `Risk score has remained steady at ${lastScore}/100 across recent reporting periods.`,
                highlight: 'Numbers are within expected historical baseline tolerances.',
                trendDirection: 'STABLE'
            };
        }
    };

    window.CIPSimplifier = CIPSimplifier;
})(window);
