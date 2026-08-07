import re

with open('frontend/components/command-center/incident-rail.tsx', 'r') as f:
    content = f.read()

start_marker = '<AnimatePresence mode="wait" initial={false}>'
end_marker = '</AnimatePresence>\n                </div>\n              </RailSurface>'

start_idx = content.find(start_marker)
end_idx = content.find(end_marker)

if start_idx == -1 or end_idx == -1:
    print("Could not find markers")
    exit(1)

new_content = """<AnimatePresence mode="wait" initial={false}>
                    <motion.div
                      key={activeTool}
                      className="incident-inspector-view w-full"
                      style={theaterSurfaceStyle}
                      initial={reduceMotion ? { opacity: 1 } : { opacity: 0, y: 10, filter: 'blur(5px)' }}
                      animate={reduceMotion ? { opacity: 1 } : { opacity: 1, y: 0, filter: 'blur(0px)' }}
                      exit={reduceMotion ? { opacity: 0 } : { opacity: 0, y: -5, filter: 'blur(2px)' }}
                      transition={{ duration: reduceMotion ? 0 : 0.2, ease: [0.22, 1, 0.36, 1] }}
                    >
                      {activeTool === 'incident' && (
                        <section className="incident-panel-flow w-full" aria-label="当前事件摘要" style={theaterSurfaceStyle}>
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus icon="warning" label="当前事件" value={severityLabel(selectedAlert.severity)} />
                                  <ToolbenchStatus icon="shield" label="判定" value={decisionLabel(selectedAlert.decision)} />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">{selectionSummary.eyebrow}</span>
                                  <h2 className="text-2xl font-light leading-snug tracking-tight text-white/90">{selectionSummary.title}</h2>
                                  <p className="mt-1 text-sm leading-relaxed text-white/50 font-light">{selectionSummary.description}</p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">风险脉冲</span>
                                    <small className="text-xs text-white/40 font-light">{selectedModel.boundaryCrossingCount} 次边界 · {selectedModel.evidenceRefs.length} 条证据</small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums"><CountUp value={pulse} /></strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]" aria-hidden="true">
                                  <motion.i className="block h-full rounded-full bg-gradient-to-r from-rose-500/80 to-orange-400/80" initial={{ width: 0 }} animate={{ width: `${pulse}%` }} transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }} />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="zap" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">下一步动作</span>
                                </div>
                                <p className="text-sm font-light leading-relaxed text-white/80">{nextAction}</p>
                                <div className="flex items-center gap-3 mt-1">
                                  <Link href={buildInvestigationHref(selectedAlert.alert_id)} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    打开调查 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                  <Link href={buildTraceHref(selectedAlert.alert_id)} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    查看链路 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">事实快照</span>
                                </div>
                                <div className="grid grid-cols-2 md:grid-cols-3 gap-y-5 gap-x-4">
                                  {factRows.map((row) => (
                                    <div key={row.label} className="flex flex-col gap-1 min-w-0">
                                      <span className="text-[10px] font-medium tracking-widest text-white/30 truncate uppercase">{row.label}</span>
                                      <span className="text-base font-light tabular-nums tracking-tight text-white/80 truncate" title={String(row.value)}>{row.value}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="shield" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">边界锚点 & 责任路径</span>
                                </div>
                                <div className="flex flex-col gap-5">
                                  <div className="flex flex-col gap-2 min-w-0">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">ANCHOR</span>
                                    <strong className="text-sm font-light tracking-wide text-white/80 leading-relaxed break-words">{boundaryLabel}</strong>
                                    <p className="text-xs text-white/40 font-light">第一处可归责边界，用于快速定位。</p>
                                  </div>
                                  <div className="h-px w-full bg-white/5" />
                                  <div className="flex flex-col gap-2 min-w-0">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">PATH PREVIEW</span>
                                    <strong className="text-sm font-light tracking-wide text-white/80 leading-relaxed break-words">{pathPreview}</strong>
                                    <p className="text-xs text-white/40 font-light">主因果链已聚焦到当前事件。</p>
                                  </div>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="graph-line" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">归责链</span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {incidentNarrative.map((item) => (
                                    <div key={item.id} className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]">
                                      <span className="text-sm font-light tracking-wide text-white/80 group-hover/label:text-white truncate pr-4">{item.label}</span>
                                      <span className="shrink-0 text-[9px] font-medium uppercase tracking-widest text-white/30">{item.meta}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'evidence' && (
                        <section className="incident-panel-flow w-full" aria-label="证据工作台" style={theaterSurfaceStyle}>
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus icon={readinessIcon} label="证据就绪" value={readiness.label} />
                                  <ToolbenchStatus icon="references" label="引用" value={`${selectedModel.evidenceRefs.length} 条`} />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">{selectionSummary.eyebrow}</span>
                                  <h2 className="text-2xl font-light leading-snug tracking-tight text-white/90">{selectionSummary.title}</h2>
                                  <p className="mt-1 text-sm leading-relaxed text-white/50 font-light">{selectionSummary.description}</p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">落证完成度</span>
                                    <small className="text-xs text-white/40 font-light">{exactEvidenceCount} 条 L1 精确 · {selectedModel.linkedEvidence.length} 条关联</small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums"><AnimatedNumber value={evidencePulse} /></strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]" aria-hidden="true">
                                  <motion.i className="block h-full rounded-full bg-gradient-to-r from-emerald-500/80 to-teal-400/80" initial={{ width: 0 }} animate={{ width: `${evidencePulse}%` }} transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }} />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="sparkle-filled" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">核心落证句</span>
                                </div>
                                <p className="text-sm font-light leading-relaxed text-white/80">
                                  {primaryEvidence === '待补强' ? '当前仍缺少主证据。' : `${primaryEvidence} 正在托住结论。`}
                                </p>
                                <div className="flex items-center gap-3 mt-1">
                                  <Link href={buildInvestigationHref(selectedAlert.alert_id)} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    打开调查 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">核心指标</span>
                                </div>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-y-5 gap-x-4">
                                  {evidenceRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex flex-col gap-1 min-w-0">
                                      <span className="text-[10px] font-medium tracking-widest text-white/30 truncate uppercase">{row.label}</span>
                                      <span className="text-base font-light tabular-nums tracking-tight text-white/80 truncate" title={String(row.value)}>{row.value}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">落证结构</span>
                                </div>
                                <div className="flex flex-wrap gap-2">
                                  {evidenceNarrative.map((item) => (
                                    <span key={item.id} className="rounded-md border border-white/5 bg-white/[0.02] px-3 py-1.5 text-xs font-light text-white/70">{item.label}</span>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="search" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">证据清单</span>
                                </div>
                                <div className="flex flex-col divide-y divide-white/[0.03]">
                                  {evidenceItems.map((item, idx) => (
                                    <motion.div key={item.id} className="group/item flex flex-col gap-2 py-4 first:pt-0 last:pb-0" initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.4 + idx * 0.08, type: "spring", stiffness: 200, damping: 20 }}>
                                      <div className="flex items-center justify-between gap-4">
                                        <span className="text-sm font-light tracking-wide text-white/80 group-hover/item:text-white break-words">{item.label}</span>
                                        {item.meta ? <span className="shrink-0 rounded-full border border-white/5 bg-white/[0.02] px-2 py-0.5 text-[9px] font-medium uppercase tracking-widest text-white/50">{item.meta}</span> : null}
                                      </div>
                                      <span className="text-xs font-light leading-relaxed text-white/40">{item.detail}</span>
                                    </motion.div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'trace' && (
                        <section className="incident-panel-flow w-full" aria-label="链路工作台" style={theaterSurfaceStyle}>
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus icon="graph-line" label="责任路径" value={`${selectedModel.pathEdges.length} 边`} />
                                  <ToolbenchStatus icon="history" label="时间序列" value={`${selectedModel.timelineSteps.length} 步`} />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">链路工作台</span>
                                  <h2 className="text-2xl font-light leading-snug tracking-tight text-white/90">把越界动作压回一条可解释的责任链</h2>
                                  <p className="mt-1 text-sm leading-relaxed text-white/50 font-light">{selectionSummary.description}</p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">链路覆盖度</span>
                                    <small className="text-xs text-white/40 font-light">{selectedModel.pathNodes.length} 节点 · {selectedModel.timelineSteps.length} 步</small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums"><CountUp value={tracePulse} /></strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]" aria-hidden="true">
                                  <motion.i className="block h-full rounded-full bg-gradient-to-r from-blue-500/80 to-indigo-400/80" initial={{ width: 0 }} animate={{ width: `${tracePulse}%` }} transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }} />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="zap" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">链路动作</span>
                                </div>
                                <p className="text-sm font-light leading-relaxed text-white/80">优先核对第一条边界穿越，再确认委派边和污染传播边是否一致。</p>
                                <div className="flex items-center gap-3 mt-1">
                                  <Link href={buildTraceHref(selectedAlert.alert_id)} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    打开链路 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="graph-line" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">核心指标</span>
                                </div>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-y-5 gap-x-4">
                                  {traceRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex flex-col gap-1 min-w-0">
                                      <span className="text-[10px] font-medium tracking-widest text-white/30 truncate uppercase">{row.label}</span>
                                      <span className="text-base font-light tabular-nums tracking-tight text-white/80 truncate" title={String(row.value)}>{row.value}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="git-commit" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">链路骨架</span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {traceNarrative.map((item) => (
                                    <div key={item.id} className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]">
                                      <span className="text-sm font-light tracking-wide text-white/80 group-hover/label:text-white truncate pr-4">{item.label}</span>
                                      <span className="shrink-0 text-[9px] font-medium uppercase tracking-widest text-white/30">{item.meta}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="list-selection" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">链路与时间序列</span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">PATH PREVIEW</span>
                                  <strong className="text-sm font-light tracking-wide text-white/80 leading-relaxed break-words">{pathPreview}</strong>
                                </div>
                                <div className="h-px w-full bg-white/5 my-2" />
                                <div className="flex flex-col gap-6">
                                  <div className="flex flex-col gap-3 min-w-0">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">责任路径</span>
                                    <SignalList items={traceItems} emptyLabel="当前事件暂无可用的回溯边。" />
                                  </div>
                                  <div className="flex flex-col gap-3 min-w-0">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">时间序列</span>
                                    <SignalList items={timelineItems} emptyLabel="当前事件暂无运行时时间线步骤。" />
                                  </div>
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'agent' && (
                        <section className="incident-panel-flow w-full" aria-label="主体工作台" style={theaterSurfaceStyle}>
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus icon="organization" label="主体" value={selectedAlert.primary_agent.agent_id} />
                                  <ToolbenchStatus icon="key" label="权限" value={selectedAlert.authority} />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">主体身份</span>
                                  <h2 className="text-2xl font-light leading-snug tracking-tight text-white/90 break-all">{selectedAlert.primary_agent.agent_id}</h2>
                                  <p className="mt-1 text-sm leading-relaxed text-white/50 font-light">声明角色 {selectedAlert.primary_agent.claimed_role ?? 'unknown'}，判定为 {decisionLabel(selectedAlert.decision)}。</p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">主体可信度</span>
                                    <small className="text-xs text-white/40 font-light">{selectedModel.involvedAgentIds.length} 个主体 · {Math.round(selectedAlert.confidence * 100)}% 置信度</small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums"><CountUp value={agentPulse} /></strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]" aria-hidden="true">
                                  <motion.i className="block h-full rounded-full bg-gradient-to-r from-amber-500/80 to-orange-400/80" initial={{ width: 0 }} animate={{ width: `${agentPulse}%` }} transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }} />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="key" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">权限检查</span>
                                </div>
                                <p className="text-sm font-light leading-relaxed text-white/80">{selectedAlert.reasoning}</p>
                                <div className="flex items-center gap-3 mt-1">
                                  <Link href={selectedModel.primaryAgentId ? buildAgentHref(selectedModel.primaryAgentId) : '/agents'} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    打开主体 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">主体特征</span>
                                </div>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-y-5 gap-x-4">
                                  {agentRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex flex-col gap-1 min-w-0">
                                      <span className="text-[10px] font-medium tracking-widest text-white/30 truncate uppercase">{row.label}</span>
                                      <span className="text-base font-light tabular-nums tracking-tight text-white/80 truncate" title={String(row.value)}>{row.value}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="organization" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">主体谱系</span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {agentNarrative.map((item) => (
                                    <div key={item.id} className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]">
                                      <span className="text-sm font-light tracking-wide text-white/80 group-hover/label:text-white truncate pr-4 break-all">{item.label}</span>
                                      <span className="shrink-0 text-[9px] font-medium uppercase tracking-widest text-white/30">{item.meta}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="layers" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">主体列表</span>
                                </div>
                                <SignalList items={agentItems} emptyLabel="当前事件没有挂接更多相关主体。" />
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}

                      {activeTool === 'handoff' && (
                        <section className="incident-panel-flow w-full" aria-label="委派工作台" style={theaterSurfaceStyle}>
                          <div className="flex flex-col gap-4 pb-12">
                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex flex-wrap items-center gap-3">
                                  <ToolbenchStatus icon="git-pull-request" label="委派边" value={`${handoffEdges.length}`} />
                                  <ToolbenchStatus icon="target" label="边界穿越" value={`${selectedModel.boundaryCrossingCount} 次`} />
                                </div>
                                <div className="flex flex-col gap-2">
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派工作台</span>
                                  <h2 className="text-2xl font-light leading-snug tracking-tight text-white/90">沿着委派链追责</h2>
                                  <p className="mt-1 text-sm leading-relaxed text-white/50 font-light">{nextAction}</p>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center justify-between">
                                  <div className="flex flex-col gap-1">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派热度</span>
                                    <small className="text-xs text-white/40 font-light">{handoffEdges.length} 条委派边 · {selectedModel.boundaryCrossingCount} 次越界</small>
                                  </div>
                                  <div className="flex items-baseline gap-1">
                                    <strong className="text-5xl font-extralight tracking-tighter text-white/90 tabular-nums"><CountUp value={handoffPulse} /></strong>
                                    <span className="text-lg font-light text-white/30">%</span>
                                  </div>
                                </div>
                                <div className="h-1 w-full overflow-hidden rounded-full bg-white/[0.03]" aria-hidden="true">
                                  <motion.i className="block h-full rounded-full bg-gradient-to-r from-purple-500/80 to-fuchsia-400/80" initial={{ width: 0 }} animate={{ width: `${handoffPulse}%` }} transition={{ delay: 0.3, duration: 0.8, ease: 'easeOut' }} />
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-4">
                                <div className="flex items-center gap-2">
                                  <Codicon name="link" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">边界动作</span>
                                </div>
                                <p className="text-sm font-light leading-relaxed text-white/80">{nextAction}</p>
                                <div className="flex items-center gap-3 mt-1">
                                  <Link href="/handoff" className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    打开委派 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                  <Link href={buildTraceHref(selectedAlert.alert_id)} className="flex-1 inline-flex items-center justify-center gap-2 rounded-lg border border-white/5 bg-white/[0.02] px-4 py-2.5 text-xs font-medium tracking-wide text-white/70 transition-all hover:bg-white/[0.06] hover:text-white">
                                    查看链路 <Codicon name="arrow-right" size={12} />
                                  </Link>
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="references" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派特征</span>
                                </div>
                                <div className="grid grid-cols-2 md:grid-cols-4 gap-y-5 gap-x-4">
                                  {handoffRows.slice(0, 4).map((row) => (
                                    <div key={row.label} className="flex flex-col gap-1 min-w-0">
                                      <span className="text-[10px] font-medium tracking-widest text-white/30 truncate uppercase">{row.label}</span>
                                      <span className="text-base font-light tabular-nums tracking-tight text-white/80 truncate" title={String(row.value)}>{row.value}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="git-pull-request" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派顺序</span>
                                </div>
                                <div className="flex flex-col gap-2">
                                  {handoffNarrative.map((item) => (
                                    <div key={item.id} className="group/label flex items-center justify-between rounded-lg border border-white/5 bg-white/[0.01] px-4 py-3 transition-all hover:border-white/20 hover:bg-white/[0.04]">
                                      <span className="text-sm font-light tracking-wide text-white/80 group-hover/label:text-white truncate pr-4 break-all">{item.label}</span>
                                      <span className="shrink-0 text-[9px] font-medium uppercase tracking-widest text-white/30">{item.meta}</span>
                                    </div>
                                  ))}
                                </div>
                              </div>
                            </BentoCard>

                            <BentoCard className="p-6">
                              <div className="flex flex-col gap-5">
                                <div className="flex items-center gap-2">
                                  <Codicon name="git-pull-request" size={14} className="text-white/30" />
                                  <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派边界 & 委派链</span>
                                </div>
                                <div className="flex flex-col gap-5">
                                  <div className="flex flex-col gap-2 min-w-0">
                                    <span className="text-[9px] tracking-[0.2em] text-white/20 uppercase">委派边界</span>
                                    <strong className="text-sm font-light tracking-wide text-white/80 leading-relaxed break-words">{boundaryLabel}</strong>
                                    <p className="text-xs text-white/40 font-light">把第一处边界突破固定成锚点，避免只停留在结果层。</p>
                                  </div>
                                  <div className="h-px w-full bg-white/5" />
                                  <div className="flex flex-col gap-3 min-w-0">
                                    <span className="text-[10px] font-medium tracking-[0.2em] text-white/40 uppercase">委派链</span>
                                    <SignalList items={handoffItems} emptyLabel="当前路径没有挂接委派或权限转移边。" />
                                  </div>
                                </div>
                              </div>
                            </BentoCard>
                          </div>
                        </section>
                      )}
                    </motion.div>
                  </AnimatePresence>"""

new_file_content = content[:start_idx] + new_content + content[end_idx + len('</AnimatePresence>'):]

with open('frontend/components/command-center/incident-rail.tsx', 'w') as f:
    f.write(new_file_content)

print("Done")
