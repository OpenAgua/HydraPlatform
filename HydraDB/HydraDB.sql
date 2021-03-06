DROP DATABASE IF EXISTS hydradb;
CREATE DATABASE hydradb;

USE hydradb;

/* ========================================================================= */
/* User permission management                                                */

CREATE TABLE tUser (
    user_id  INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    username varchar(60) NOT NULL,
    password varchar(1000) NOT NULL,
    display_name varchar(60) NOT NULL default '', 
    last_login DATETIME,
    last_edit  DATETIME,
    UNIQUE (username),
    cr_date  TIMESTAMP default localtimestamp
);
create index iUser on tUser(username);

CREATE TABLE tRole (
    role_id   INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    role_code VARCHAR(60) NOT NULL,
    role_name VARCHAR(60) NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    UNIQUE (role_code)
);
create index iRole1 on tRole(role_code);

CREATE TABLE tPerm (
    perm_id   INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    perm_code VARCHAR(60) NOT NULL,
    perm_name VARCHAR(60) NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    UNIQUE (perm_code),
    INDEX (perm_code)
);
create index iPerm1 on tPerm(perm_code);

CREATE TABLE tRoleUser (
    user_id INT NOT NULL,
    role_id INT NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    PRIMARY KEY (user_id, role_id),
    FOREIGN KEY (user_id) REFERENCES tUser(user_id),
    FOREIGN KEY (role_id) REFERENCES tRole(role_id)
);

CREATE TABLE tRolePerm (
    perm_id INT NOT NULL,
    role_id INT NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    PRIMARY KEY (perm_id, role_id),
    FOREIGN KEY (perm_id) REFERENCES tPerm(perm_id),
    FOREIGN KEY (role_id) REFERENCES tRole(role_id)
);

/* Project network and scenearios */

CREATE TABLE tProject (
    project_id          INT           NOT NULL PRIMARY KEY AUTO_INCREMENT,
    project_name        VARCHAR(60)   NOT NULL,
    project_description VARCHAR(1000),
    status              VARCHAR(1) default 'A' NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    created_by          INT,
    UNIQUE (project_name),
    constraint chk_status check (status in ('A', 'X')),
    FOREIGN KEY (created_by) REFERENCES tUser(user_id)
);

insert into tProject (project_name) values ('Default Project');

CREATE TABLE tNetwork (
    network_id          INT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    network_name        VARCHAR(60)  NOT NULL,
    network_description VARCHAR(1000),
    network_layout      TEXT,
    project_id          INT          NOT NULL,
    status              VARCHAR(1) default 'A' NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    projection          VARCHAR(1000),
    created_by          INT,
    FOREIGN KEY (created_by) REFERENCES tUser(user_id),
    FOREIGN KEY (project_id) REFERENCES tProject(project_id),
    constraint chk_status check (status in ('A', 'X')),
    UNIQUE (project_id, network_name)
);

insert into tNetwork(project_id, network_name) values (1, 'Default Network');

CREATE TABLE tNode (
    node_id          INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    network_id       INT         NOT NULL,
    node_description VARCHAR(1000),
    node_name        VARCHAR(60) NOT NULL,
    status           VARCHAR(1) default 'A' NOT NULL,
    node_x           DOUBLE,
    node_y           DOUBLE,
    node_layout      TEXT,
    cr_date  TIMESTAMP default localtimestamp,
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id),
    constraint chk_status check (status in ('A', 'X')),
    UNIQUE (network_id, node_name)
);

CREATE TABLE tLink (
    link_id         INT          NOT NULL PRIMARY KEY AUTO_INCREMENT,
    network_id      INT          NOT NULL,
    status          VARCHAR(1) default 'A' NOT NULL,
    node_1_id       INT          NOT NULL,
    node_2_id       INT          NOT NULL,
    link_name       VARCHAR(60),
    link_description VARCHAR(1000),
    link_layout     TEXT,
    cr_date  TIMESTAMP default localtimestamp,
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id),
    FOREIGN KEY (node_1_id) REFERENCES tNode(node_id),
    FOREIGN KEY (node_2_id) REFERENCES tNode(node_id),
    constraint chk_status check (status in ('A', 'X')),
    UNIQUE (node_1_id, node_2_id, link_id),
    UNIQUE (network_id, link_name)
);

CREATE TABLE tScenario (
    scenario_id          INT           NOT NULL PRIMARY KEY AUTO_INCREMENT,
    scenario_name        VARCHAR(60)   NOT NULL,
    scenario_description VARCHAR(1000),
    status               VARCHAR(1) default 'A' NOT NULL,
    network_id           INT,
    start_time           DECIMAL(30, 20) UNSIGNED,
    end_time             DECIMAL(30, 20) UNSIGNED,
    time_step            VARCHAR(60),
    locked               VARCHAR(1) default 'N' NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id),
    constraint chk_status check (status in ('A', 'X')),
    constraint chk_locked check (locked in ('Y', 'N')),
    UNIQUE (network_id, scenario_name)
);

/*"Project scenario" (scenario_id 1) is the container for all project-related attributes.
  As data must be contained in a scenario, but projects do not have scenarios.
  This one scenario is a special case, only for project attributes.
*/
insert into tScenario (network_id, scenario_name) values (1, 'Project Scenario');

CREATE TABLE tResourceGroup (
    group_id          INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    group_name        VARCHAR(60) NOT NULL,
    group_description VARCHAR(1000),
    status            VARCHAR(1),
    cr_date           TIMESTAMP default localtimestamp,
    network_id        INT NOT NULL,
    constraint chk_status check (status in ('A', 'X')),
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id)
);

CREATE TABLE tResourceGroupItem(
    item_id            INT NOT NULL PRIMARY KEY AUTO_INCREMENT,
    node_id            INT,
    link_id            INT,
    subgroup_id        INT,
    ref_key            VARCHAR(60) NOT NULL,
    group_id           INT NOT NULL,
    scenario_id        INT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES tResourceGroup(group_id),
    FOREIGN KEY (scenario_id) REFERENCES tScenario(scenario_id),
    CHECK (ref_key in ('GROUP', 'NODE', 'LINK')),
    FOREIGN KEY (node_id)    REFERENCES tNode(node_id),
    FOREIGN KEY (link_id)    REFERENCES tLink(link_id),
    FOREIGN KEY (subgroup_id)  REFERENCES tResourceGroup(group_id),
    CHECK ((subgroup_id is not null and node_id is null and link_id is null) or (subgroup_id is null and node_id is not null and link_id is null) or (subgroup_id is null and node_id is null and link_id is not null))
);

CREATE TABLE tDataset (
    dataset_id  INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    data_type   VARCHAR(60) NOT NULL,
    data_units  VARCHAR(60),
    data_dimen  VARCHAR(60),
    data_name   VARCHAR(60) NOT NULL,
    data_hash   BIGINT      NOT NULL,
    cr_date     TIMESTAMP default localtimestamp,
    created_by  INT,
    locked      VARCHAR(1) NOT NULL default 'N',

    start_time  DECIMAL(30, 20) UNSIGNED,
    frequency   DOUBLE,
    value       BLOB,

    FOREIGN KEY (created_by) REFERENCES tUser(user_id),
    constraint chk_type check (data_type in ('descriptor', 'timeseries',
    'eqtimeseries', 'scalar', 'array')),
    CHECK (locked in ('Y', 'N')),
    UNIQUE (data_hash)
);

/* Attributes */

CREATE TABLE tAttr (
    attr_id    INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    attr_name  VARCHAR(60) NOT NULL,
    attr_dimen VARCHAR(60),
    cr_date  TIMESTAMP default localtimestamp,
    UNIQUE (attr_name, attr_dimen)
);

CREATE TABLE tTemplate (
    template_id   INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    template_name VARCHAR(60) NOT NULL,
    layout        TEXT,
    UNIQUE(template_name)
);

insert into tTemplate (template_name) values ('Default');

CREATE TABLE tTemplateType(
    type_id       INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    type_name     VARCHAR(60) NOT NULL,
    resource_type VARCHAR(60),
    template_id   INT,
    alias         varchar(60),
    layout        TEXT,
    FOREIGN KEY (template_id) REFERENCES tTemplate(template_id),
    UNIQUE(template_id, type_name)
);

CREATE TABLE tTypeAttr (
    attr_id     INT NOT NULL,
    type_id INT NOT NULL,
    default_dataset_id  INT,
    attr_is_var VARCHAR(1) default 'N',
    data_type   VARCHAR(60),
    data_restriction VARCHAR(1000),
    dimension   VARCHAR(60),
    PRIMARY KEY (attr_id, type_id),
    FOREIGN KEY (attr_id) REFERENCES tAttr(attr_id),
    FOREIGN KEY (type_id) REFERENCES tTemplateType(type_id),
    FOREIGN KEY (default_dataset_id) REFERENCES tDataset(dataset_id),
    constraint chk_data_type check (data_type in ('descriptor', 'timeseries',
    'eqtimeseries', 'scalar', 'array')),
    check (is_var in ('Y', 'N'))
);

CREATE TABLE tAttrMap (
    attr_id_a INT NOT NULL,
    attr_id_b INT NOT NULL,
    PRIMARY KEY (attr_id_a, attr_id_b),
    FOREIGN KEY (attr_id_a) REFERENCES tAttr(attr_id),
    FOREIGN KEY (attr_id_b) REFERENCES tAttr(attr_id)
);

CREATE TABLE tResourceType (
    resource_type_id INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    ref_key          VARCHAR(60) NOT NULL,
    network_id       INT,
    node_id          INT,
    link_id          INT,
    group_id         INT,
    type_id      int         NOT NULL,
    FOREIGN KEY (type_id) REFERENCES tTemplateType(type_id),
    CHECK (ref_key in ('GROUP', 'NODE', 'LINK', 'NETWORK')),
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id),
    FOREIGN KEY (node_id)    REFERENCES tNode(node_id),
    FOREIGN KEY (link_id)    REFERENCES tLink(link_id),
    FOREIGN KEY (group_id)   REFERENCES tResourceGroup(group_id),
    CHECK ((group_id is not null and network_id is null and node_id is null and link_id is null) or (group_id is null and network_id is not null and node_id is null and link_id is null) or (group_id is null and network_id is null and node_id is not null and link_id is null) or (group_id is null and network_id is null and node_id is null and link_id is not null))
);

CREATE TABLE tResourceAttr (
    resource_attr_id INT         NOT NULL PRIMARY KEY AUTO_INCREMENT,
    attr_id          INT         NOT NULL,
    ref_key          VARCHAR(60) NOT NULL,
    project_id       INT,
    network_id       INT,
    node_id          INT,
    link_id          INT,
    group_id         INT,
    attr_is_var      VARCHAR(1)  NOT NULL default 'N',
    FOREIGN KEY (attr_id) REFERENCES tAttr(attr_id),
    CHECK (ref_key in ('GROUP', 'NODE', 'LINK', 'NETWORK', 'PROJECT')),
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id),
    FOREIGN KEY (node_id)    REFERENCES tNode(node_id),
    FOREIGN KEY (link_id)    REFERENCES tLink(link_id),
    FOREIGN KEY (group_id)   REFERENCES tResourceGroup(group_id),
    CHECK ((group_id is not null and network_id is null and node_id is null and link_id is null ) or (group_id is null and network_id is not null and node_id is null and link_id is null) or (group_id is null and network_id is null and node_id is not null and link_id is null) or (group_id is null and network_id is null and node_id is null and link_id is not null))
);

/* Data representation */

CREATE TABLE tTimeSeriesData(
    dataset_id  INT NOT NULL,
    ts_time     DECIMAL(30, 20) UNSIGNED NOT NULL,
    ts_value    BLOB  NOT NULL,
    PRIMARY KEY (dataset_id, ts_time),
    FOREIGN KEY (dataset_id) references tDataset(dataset_id)
);

CREATE TABLE tDatasetGroup (
    group_id    INT     NOT NULL PRIMARY KEY AUTO_INCREMENT,
    group_name  VARCHAR(60) NOT NULL,
    cr_date     TIMESTAMP default localtimestamp,
    UNIQUE (group_name)
);

CREATE TABLE tDatasetGroupItem (
    group_id    INT    NOT NULL,
    dataset_id  INT    NOT NULL,
    PRIMARY KEY (group_id, dataset_id),
    FOREIGN KEY (group_id) REFERENCES tDatasetGroup(group_id),
    FOREIGN KEY (dataset_id) REFERENCES tDataset(dataset_id)
);

CREATE TABLE tMetadata (
    dataset_id  INT         NOT NULL,
    metadata_name VARCHAR(60) NOT NULL,
    metadata_val  BLOB      NOT NULL,
    PRIMARY KEY (dataset_id, metadata_name),
    FOREIGN KEY (dataset_id) REFERENCES tDataset(dataset_id)
);

CREATE TABLE tResourceScenario (
    dataset_id          INT NOT NULL,
    scenario_id      INT NOT NULL,
    resource_attr_id INT NOT NULL,
    PRIMARY KEY (resource_attr_id, scenario_id),
    FOREIGN KEY (scenario_id) REFERENCES tScenario(scenario_id),
    FOREIGN KEY (dataset_id) REFERENCES tDataset(dataset_id),
    FOREIGN KEY (resource_attr_id) REFERENCES tResourceAttr(resource_attr_id)
);

/*Ownership*/
CREATE TABLE tProjectOwner (
    user_id  INT NOT NULL,
    project_id INT,
    view     VARCHAR(1) NOT NULL,
    edit     VARCHAR(1) NOT NULL,
    share     VARCHAR(1) NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    PRIMARY KEY (user_id, project_id),
    CHECK (view in ('Y','N')),
    CHECK (edit in ('Y','N')),
    CHECK (share in ('Y','N')),
    FOREIGN KEY (user_id) REFERENCES tUser(user_id),
    FOREIGN KEY (project_id) REFERENCES tProject(project_id)
);

CREATE TABLE tNetworkOwner (
    user_id  INT NOT NULL,
    network_id INT,
    view     VARCHAR(1) NOT NULL,
    edit     VARCHAR(1) NOT NULL,
    share     VARCHAR(1) NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    PRIMARY KEY (user_id, network_id),
    CHECK (view in ('Y','N')),
    CHECK (edit in ('Y','N')),
    CHECK (share in ('Y','N')),
    FOREIGN KEY (user_id) REFERENCES tUser(user_id),
    FOREIGN KEY (network_id) REFERENCES tNetwork(network_id)
);

CREATE TABLE tDatasetOwner (
    user_id  INT NOT NULL,
    dataset_id INT,
    view     VARCHAR(1) NOT NULL,
    edit     VARCHAR(1) NOT NULL,
    share     VARCHAR(1) NOT NULL,
    cr_date  TIMESTAMP default localtimestamp,
    PRIMARY KEY (user_id, dataset_id),
    CHECK (view in ('Y','N')),
    CHECK (edit in ('Y','N')),
    CHECK (share in ('Y','N')),
    FOREIGN KEY (user_id) REFERENCES tUser(user_id),
    FOREIGN KEY (dataset_id) REFERENCES tDataset(dataset_id)
);

source data.sql;
