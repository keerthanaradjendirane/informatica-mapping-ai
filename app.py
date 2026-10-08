                st.subheader("1. Purpose and Business Outcome")
                st.info(functional_overview(mapping))
                st.subheader("2. Process Flow")
                steps = ordered_processing_steps(mapping)
                friendly = [object_label(primary_source(mapping) or {}, "Source")]
                friendly.extend({
                    "Source Qualifier": "Prepare order data",
                    "Lookup": "Find customer information",
                    "Expression": "Calculate order details",
                    "Router": "Route records",
                    "Filter": "Keep qualifying records",
                    "Joiner": "Combine related information",
                    "Sequence Generator": "Create tracking value",
                    "Sorter": "Arrange records",
                    "Rank": "Identify highest-priority orders",
                    "Aggregator": "Summarize information",
                }.get(s.get("type"), functional_step_text(s)) for s in steps)
                friendly.append(object_label(primary_target(mapping) or {}, "Target"))
                flow_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">'
                for i, label in enumerate(friendly, 1):
                    flow_html += f'<div style="background:#18212d;border:1px solid #59677a;border-radius:10px;padding:9px 12px;min-width:150px;text-align:center;"><b>{i}.</b> {escape(str(label))}</div>'
                    if i < len(friendly): flow_html += '<div style="font-size:20px;">→</div>'
                flow_html += '</div>'
                st.markdown(flow_html, unsafe_allow_html=True)
                st.subheader("3. What Happens at Each Stage")
                for i, step in enumerate(steps, 1):
                    with st.expander(f"{i}. {functional_step_text(step)}", expanded=False):
                        st.write(f"This stage is responsible for: {functional_step_text(step)}")
                st.subheader("4. Logic Applied")
                filters = [format_filter(x) for x in mapping.get("filters", []) if format_filter(x)]
                if filters:
                    for f in filters:
                        if "TO_DECIMAL(orderValue) > 0" in f:
                            st.write("• Only orders with a positive order value continue.")
                        elif "RANKINDEX" in f and "<= 3" in f:
                            st.write("• Only the top three ranked records continue.")
                        else:
                            st.write(f"• Records continue only when the configured condition is satisfied: {f}")
                else:
                    st.info("No selection condition was captured.")
                st.subheader("5. Final Result")
                st.success(f"The processed information is delivered to **{object_label(primary_target(mapping) or {}, 'the destination')}**.")
            else:
                st.subheader("1. Technical Overview")
                st.info(technical_overview(mapping))
                st.subheader("2. Execution Order")
                tech_steps = ordered_processing_steps(mapping)
                labels = [object_label(primary_source(mapping) or {}, "Source")] + [s.get("name", "Step") for s in tech_steps] + [object_label(primary_target(mapping) or {}, "Target")]
                flow_html = '<div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center;">'
                for i, label in enumerate(labels, 1):
                    flow_html += f'<div style="background:#18212d;border:1px solid #59677a;border-radius:10px;padding:9px 12px;min-width:150px;text-align:center;"><b>{i}.</b> {escape(str(label))}</div>'
                    if i < len(labels): flow_html += '<div style="font-size:20px;">→</div>'
                flow_html += '</div>'
                st.markdown(flow_html, unsafe_allow_html=True)
                st.subheader("3. Source and Target")
                st.table({
                    "Role": ["Source", "Target"],
                    "Object": [object_label(primary_source(mapping) or {}, "Source"), object_label(primary_target(mapping) or {}, "Target")],
                    "Connection": [connection_display(primary_source(mapping) or {}), connection_display(primary_target(mapping) or {})],
                    "Database / Schema": [database_display(primary_source(mapping) or {}), database_display(primary_target(mapping) or {})],
                })
                st.subheader("4. Logic Applied by Transformation")
                for i, step in enumerate(tech_steps, 1):
                    with st.expander(f"{i}. {step.get('name','')} — {step.get('type','Transformation')}", expanded=False):
                        for logic in transformation_all_logic(step):
                            st.code(logic, language="text")
                st.subheader("5. Lookup and Join Conditions")
                relation_steps = [s for s in tech_steps if s.get("type") in {"Lookup", "Joiner"}]
                if relation_steps:
                    for step in relation_steps:
                        st.markdown(f"**{step.get('name','')}**")
                        for logic in transformation_all_logic(step):
                            st.code(logic, language="text")
                else:
                    st.info("No lookup or join transformation was captured.")
                st.subheader("6. Field Mappings")
                if mapping.get("field_mappings"):
                    rows=[]
                    for item in mapping["field_mappings"]:
                        loc=mapping_location_for_field(mapping,item)
                        rows.append({"Source field":item.get("source_field", "Unresolved"),"Applied at":loc.get("target_transformation","Unresolved"),"Target field":item.get("target_field", "Unresolved")})
                    st.dataframe(rows,use_container_width=True,hide_index=True)
                else:
                    st.info("No field mappings were captured.")

    # --------------------------------------------------------
    # 8. TECHNICAL DETAILS
    # --------------------------------------------------------

    with tabs[7]:
        st.header("⚙️ Technical Details")

        st.caption(
            "Developer-oriented information. The business views above "
            "are intentionally kept free of raw Informatica JSON."
        )

        with st.expander("Normalized Metadata"):
            st.json(
                {
                    k: v
                    for k, v in mapping.items()
                    if k != "raw"
                }
            )

        with st.expander("Raw Informatica Metadata"):
            st.json(mapping.get("raw", {}))

else:
    st.info(
        "Start by clicking **Open / Connect**, complete Informatica login "
        "in the browser, then click **Discover Projects / Folders / Mappings**."
    )

    st.markdown(
        """
        ### What this version is designed to do

        1. Discover Informatica Projects → Folders → Mappings.
        2. Load a selected mapping.
        3. Explain the mapping in business-friendly language.
        4. Show exactly where each field comes from and where it goes.
        5. Show the source/connection and target/connection when captured.
        6. Validate the mapping and explain structural errors.
        7. Provide a complete developer Debugger and troubleshooting view.
        8. Simulate Add/Delete change impact without modifying Informatica.
        9. Let users ask normal questions or get emergency AI troubleshooting help.
